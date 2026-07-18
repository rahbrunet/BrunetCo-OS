"""Watcher domain core (WP 6.2, spec §A1) — presence-diff state + OS-native outputs.

Ports the decision logic of the legacy CIPO monitor (/opt/cipo-monitor scheduler.py + main.py)
onto DB-backed state. The scrape engine itself lives in the workers app; everything here is
browser-free so the golden tests run against Postgres alone.

Presence-diff (legacy #1): date-based "new" detection fails on CIPO because doc dates are
date-only (afternoon docs share a morning run's date) and some docs are back-dated (registration
notices appear weeks after their printed date). So a document is NEW iff its identity key is
absent from the matter's seen-set. Only a matter with no prior state falls back to a date cutoff
(bootstrap), and the baseline is seeded immediately after.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg


@dataclass(frozen=True)
class DocRow:
    """One row of a CIPO documents table (newest first, table order)."""

    description: str
    date_str: str  # 'YYYY-MM-DD' as scraped; may be empty on parse failure


@dataclass
class MonitorableMatter:
    matter_id: UUID
    family_id: UUID
    application_no: str
    handled_by_others: bool


# --- identity + selection (pure; ports scheduler.doc_key / select_new_docs) ----

def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def doc_key(doc: DocRow) -> str:
    """Stable identity for a document row: normalized date + description. CIPO exposes no unique
    doc id, so (date, description) is the best available fingerprint; whitespace/case are
    normalized so trivial scrape variations don't make an old doc look new."""
    return f"{_norm_key(doc.date_str)}|{_norm_key(doc.description)}"


def _is_on_or_after(doc_date_str: str, cutoff: date) -> bool:
    """>= (not >): doc dates and run dates are both date-only, so a strict > would silently drop
    every doc that posted on the same calendar day as a run."""
    try:
        doc_date = datetime.strptime(doc_date_str.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return False
    return doc_date >= cutoff


def select_new_docs(
    docs: list[DocRow],
    seen: set[str],
    last_run: date,
    bootstrap_grace_days: int = 0,
) -> list[int]:
    """Indices of docs considered new for one matter.

    Normal case (prior seen-state exists): pure presence-diff — new iff the identity key is not
    in ``seen``; catches same-day and back-dated docs alike, independent of the date.

    Bootstrap case (no prior state): nothing to diff against, so fall back to a date cutoff of
    (last_run - bootstrap_grace_days) using >=. The caller then seeds the baseline with all
    current docs, so this branch runs at most once per matter.
    """
    if seen:
        return [i for i, d in enumerate(docs) if doc_key(d) not in seen]
    cutoff = last_run - timedelta(days=bootstrap_grace_days)
    return [i for i, d in enumerate(docs) if _is_on_or_after(d.date_str, cutoff)]


# --- DB state ------------------------------------------------------------------

def load_seen(conn: psycopg.Connection, matter_id: UUID) -> set[str]:
    rows = conn.execute(
        "select doc_key from app.watcher_seen_docs where matter_id = %s", (matter_id,)
    ).fetchall()
    return {r[0] for r in rows}


def seed_seen(conn: psycopg.Connection, matter_id: UUID, keys: set[str]) -> None:
    """Extend the matter's baseline (idempotent) so today's docs are not re-flagged next run."""
    for key in sorted(keys):
        conn.execute(
            "insert into app.watcher_seen_docs (matter_id, doc_key) values (%s, %s) "
            "on conflict do nothing",
            (matter_id, key),
        )


def monitorable_matters(
    conn: psycopg.Connection,
    jurisdiction_code: str = "CA",
    limit: int | None = None,
    application_nos: list[str] | None = None,
) -> list[MonitorableMatter]:
    """CIPO-monitorable matters: the jurisdiction's matters that carry an application number.

    Skip rule (legacy #8): 'abandoned' is skipped entirely; 'client_abandoned' (D35 enum) is
    STILL monitored — the client walked away, but the firm watches the record. Expired/closed
    are dead records and skipped too. ``application_nos`` narrows the sweep to specific
    applications (targeted recheck of CIPO-500 rows).
    """
    rows = conn.execute(
        """
        select m.id, m.family_id, m.application_no, m.handled_by_others
          from app.matters m
         where m.jurisdiction_code = %s
           and m.application_no is not null
           and m.status not in ('abandoned', 'expired', 'closed')
           and (%s::text[] is null or m.application_no = any(%s))
         order by m.created_at
         limit coalesce(%s, 2147483647)
        """,
        (jurisdiction_code, application_nos, application_nos, limit),
    ).fetchall()
    return [
        MonitorableMatter(
            matter_id=r[0], family_id=r[1],
            application_no=str(r[2]).replace(",", "").replace(" ", "").strip(),
            handled_by_others=r[3],
        )
        for r in rows
    ]


# --- run log -------------------------------------------------------------------

def start_run(conn: psycopg.Connection, agent_name: str) -> UUID:
    row = conn.execute(
        "insert into ops.watcher_runs (agent_name) values (%s) returning id", (agent_name,)
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def finish_run(
    conn: psycopg.Connection, run_id: UUID, stats: dict[str, Any], status: str = "completed",
) -> None:
    conn.execute(
        "update ops.watcher_runs set finished_at = now(), status = %s, stats = %s where id = %s",
        (status, json.dumps(stats), run_id),
    )


def last_completed_run_date(conn: psycopg.Connection, agent_name: str) -> date | None:
    """Date of the last completed run — the bootstrap date cutoff (was last_run.txt)."""
    row = conn.execute(
        "select max(started_at)::date from ops.watcher_runs "
        "where agent_name = %s and status = 'completed'",
        (agent_name,),
    ).fetchone()
    return row[0] if row else None


def record_failure(
    conn: psycopg.Connection, matter_id: UUID, run_id: UUID | None, tag: str, detail: str,
) -> None:
    conn.execute(
        "insert into app.watcher_failures (matter_id, run_id, tag, detail) "
        "values (%s, %s, %s, %s)",
        (matter_id, run_id, tag, detail[:2000]),
    )


# --- OS-native outputs (M5 document + M1 task + M1-R14 provenance) --------------

def capture_document(
    conn: psycopg.Connection,
    matter_id: UUID,
    filename: str,
    content_hash: str,
    doc_date: date | None,
) -> tuple[UUID, bool]:
    """File a downloaded CIPO PDF as an OS document record (D41), hash-deduplicated (M6-R8).

    Returns (document_id, created). An identical file (same sha256) reuses the existing record
    and only ensures the matter link; the driveItem pointer stays null until the Phase-4
    SharePoint uploader claims it (D41: 'null only pre-upload').
    """
    existing = conn.execute(
        "select id from app.documents where content_hash = %s limit 1", (content_hash,)
    ).fetchone()
    if existing is not None:
        document_id = UUID(str(existing[0]))
        created = False
    else:
        row = conn.execute(
            """
            insert into app.documents (filename, doc_type, source, doc_date, content_hash)
            values (%s, 'OA', 'office_correspondence', %s, %s) returning id
            """,
            (filename, doc_date, content_hash),
        ).fetchone()
        assert row is not None
        document_id = UUID(str(row[0]))
        created = True
    conn.execute(
        """
        insert into app.document_links (document_id, matter_id, is_primary)
        values (%s, %s, %s)
        on conflict (document_id, matter_id) where matter_id is not null do nothing
        """,
        (document_id, matter_id, created),
    )
    return document_id, created


def create_watcher_task(
    conn: psycopg.Connection,
    matter_id: UUID,
    family_id: UUID,
    application_no: str,
    doc: DocRow,
    run_id: UUID,
    agent_name: str = "cipo-watcher",
) -> tuple[UUID, UUID] | None:
    """One docket task + one M1-R14 provenance record per newly-detected document.

    Idempotent: the provenance source_ref carries the doc's identity key, and an existing
    provenance row for (matter, source_ref) means this doc was already docketed — a crashed
    run that re-scrapes the matter must not double-generate (legacy interim-save discipline).
    Returns None on the idempotent skip.
    """
    source_ref = f"cipo:{application_no}:{doc_key(doc)}"
    dup = conn.execute(
        "select 1 from app.task_provenance where matter_id = %s and source_ref = %s",
        (matter_id, source_ref),
    ).fetchone()
    if dup is not None:
        return None

    try:
        ref_date: date | None = datetime.strptime(doc.date_str.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        ref_date = None

    task_id = uuid4()
    conn.execute(
        """
        insert into app.tasks (id, matter_id, title, deadline_type, ref_date, generated_by)
        values (%s, %s, %s, 'event', %s, 'agent')
        """,
        (task_id, matter_id, f"New CIPO document: {doc.description}", ref_date),
    )
    provenance_id = uuid4()
    conn.execute(
        """
        insert into app.task_provenance
          (id, task_id, matter_id, family_id, trigger_type, trigger_id, input_dates,
           calculated_dates, generated_by, source_ref)
        values (%s, %s, %s, %s, 'watcher', %s, %s, '{}'::jsonb, 'agent', %s)
        """,
        (provenance_id, task_id, matter_id, family_id, str(run_id),
         json.dumps({"doc_date": doc.date_str, "description": doc.description,
                     "agent": agent_name}),
         source_ref),
    )
    return task_id, provenance_id
