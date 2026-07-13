"""Prior-art references + citation linking (WP 4A.1, M11).

Pure helpers + thin DB operations for the reference database and its many-to-many cross-linking to
matters/families with §1.56 citation states. Biblio auto-fill (fetching title/inventors/dates from
an office) is an external adapter added later; here we normalize + store whatever is supplied.

normalize_citation is the dedup key: the same patent document is ONE reference row, cross-linked
from every matter that cites it (family-wide OAs, shared IDS bundles).
"""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import psycopg

VALID_STATES = {"to_disclose", "disclosed", "considered", "not_relevant", "withdrawn"}


def normalize_citation(citation: str, kind: str = "patent") -> str:
    """Canonical form for dedup. Patent numbers upper-cased with spaces/punctuation stripped
    ('us 1,234,567 b2' → 'US1234567B2'); NPL citations are just whitespace-collapsed + trimmed."""
    c = (citation or "").strip()
    if not c:
        raise ValueError("citation is empty")
    if kind == "patent":
        return re.sub(r"[\s,.\-/]", "", c).upper()
    return re.sub(r"\s+", " ", c)


def upsert_reference(
    conn: psycopg.Connection, citation: str, created_by: str, *,
    kind: str = "patent", title: str | None = None, inventors: str | None = None,
    assignee: str | None = None, pub_date: str | None = None,
    biblio: dict[str, Any] | None = None,
) -> tuple[UUID, bool]:
    """Insert a reference (or return the existing one for this normalized citation). Returns
    (reference_id, created). Idempotent on the normalized citation — same document, one row."""
    norm = normalize_citation(citation, kind)
    existing = conn.execute(
        "select id from app.prior_art_references where upper(citation) = %s", (norm,)
    ).fetchone()
    if existing is not None:
        return UUID(str(existing[0])), False
    row = conn.execute(
        """
        insert into app.prior_art_references
          (kind, citation, title, inventors, assignee, pub_date, biblio, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s) returning id
        """,
        (kind, norm, title, inventors, assignee, pub_date, json.dumps(biblio or {}), created_by),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0])), True


def link_reference(
    conn: psycopg.Connection, reference_id: UUID, matter_id: UUID, linked_by: str, *,
    citation_state: str = "to_disclose", ids_bundle: str | None = None,
    notes: str | None = None,
) -> UUID:
    """Cross-link a reference to a matter with a citation state (the matter's family is resolved
    here for the denormalized family_id). Idempotent on (reference, matter): a re-link updates the
    state/bundle rather than duplicating. Raises LookupError if the matter is not visible (RLS)."""
    if citation_state not in VALID_STATES:
        raise ValueError(f"invalid citation_state {citation_state!r}")
    matter = conn.execute(
        "select family_id from app.matters where id = %s", (matter_id,)
    ).fetchone()
    if matter is None:
        raise LookupError("matter not found or not visible")
    family_id = matter[0]
    row = conn.execute(
        """
        insert into app.reference_links
          (reference_id, matter_id, family_id, citation_state, ids_bundle, notes, linked_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (reference_id, matter_id)
          do update set citation_state = excluded.citation_state,
                        ids_bundle = coalesce(excluded.ids_bundle, app.reference_links.ids_bundle),
                        notes = coalesce(excluded.notes, app.reference_links.notes)
        returning id
        """,
        (reference_id, matter_id, family_id, citation_state, ids_bundle, notes, linked_by),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def build_matrix(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Assemble a cross-citation matrix from citation rows (WP 4A.2). Pure → unit-testable.

    Each input row is (reference_id, citation, ref_title, matter_id, matter_reference,
    citation_state). Returns {references: [{id, citation, title}], matters: [{id, reference}],
    cells: {reference_id: {matter_id: citation_state}}} — the references × matters grid the UI
    renders, with a state in each populated cell and absent keys where a reference isn't cited.
    """
    references: dict[str, dict[str, Any]] = {}
    matters: dict[str, dict[str, Any]] = {}
    cells: dict[str, dict[str, str]] = {}
    for ref_id, citation, ref_title, matter_id, matter_ref, state in rows:
        rid, mid = str(ref_id), str(matter_id)
        references.setdefault(rid, {"id": rid, "citation": citation, "title": ref_title})
        matters.setdefault(mid, {"id": mid, "reference": matter_ref})
        cells.setdefault(rid, {})[mid] = state
    return {
        "references": sorted(references.values(), key=lambda r: r["citation"]),
        "matters": sorted(matters.values(), key=lambda m: m["reference"]),
        "cells": cells,
    }


def bulk_link_family(
    conn: psycopg.Connection, reference_id: UUID, family_id: UUID, linked_by: str, *,
    citation_state: str = "to_disclose", ids_bundle: str | None = None,
) -> int:
    """Cross-cite a reference to EVERY matter in a family the caller can see (WP 4A.2 bulk
    cross-cite — family-wide OAs). RLS scopes which matters are touched; returns the count linked.
    Idempotent per matter (re-links update state)."""
    if citation_state not in VALID_STATES:
        raise ValueError(f"invalid citation_state {citation_state!r}")
    matters = conn.execute(
        "select id from app.matters where family_id = %s", (family_id,)
    ).fetchall()
    n = 0
    for (matter_id,) in matters:
        link_reference(
            conn, reference_id, matter_id, linked_by,
            citation_state=citation_state, ids_bundle=ids_bundle,
        )
        n += 1
    return n


def set_citation_state(
    conn: psycopg.Connection, link_id: UUID, citation_state: str
) -> bool:
    """Advance a link's §1.56 citation state (to_disclose → disclosed → considered, etc.).
    Returns False if the link is not found/visible."""
    if citation_state not in VALID_STATES:
        raise ValueError(f"invalid citation_state {citation_state!r}")
    row = conn.execute(
        "update app.reference_links set citation_state = %s where id = %s returning id",
        (citation_state, link_id),
    ).fetchone()
    return row is not None
