"""Prior-art reference + citation endpoints (WP 4A.1, M11).

The reference database and its many-to-many cross-linking to matters/families with §1.56 citation
states. References are firm-general; links follow matter visibility (a citation on a restricted
family is only visible to those who can see it). Feeds the WP 4A.2 cross-citation matrix + duty
dashboard.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from py_shared.domain.prior_art import (
    link_reference,
    set_citation_state,
    upsert_reference,
)
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/prior-art", tags=["prior-art"])

CitationState = Literal["to_disclose", "disclosed", "considered", "not_relevant", "withdrawn"]


class ReferenceIn(BaseModel):
    citation: str
    kind: Literal["patent", "npl"] = "patent"
    title: str | None = None
    inventors: str | None = None
    assignee: str | None = None
    pub_date: date | None = None
    biblio: dict[str, Any] = {}


class ReferenceOut(BaseModel):
    id: UUID
    created: bool


class LinkIn(BaseModel):
    reference_id: UUID
    matter_id: UUID
    citation_state: CitationState = "to_disclose"
    ids_bundle: str | None = None
    notes: str | None = None


class LinkOut(BaseModel):
    id: UUID


class StateIn(BaseModel):
    citation_state: CitationState


class CitationRow(BaseModel):
    link_id: UUID
    reference_id: UUID
    citation: str
    kind: str
    title: str | None
    matter_id: UUID
    matter_reference: str
    family_id: UUID
    citation_state: str
    ids_bundle: str | None
    linked_at: datetime


@router.post("/references", response_model=ReferenceOut, status_code=201)
def add_reference(body: ReferenceIn, identity: Identity) -> ReferenceOut:
    """Add a reference (idempotent on the normalized citation — one row per document)."""
    try:
        with identity.connection() as conn:
            ref_id, created = upsert_reference(
                conn, body.citation, identity.entra.os_user_id, kind=body.kind, title=body.title,
                inventors=body.inventors, assignee=body.assignee,
                pub_date=body.pub_date.isoformat() if body.pub_date else None, biblio=body.biblio,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    return ReferenceOut(id=ref_id, created=created)


@router.post("/links", response_model=LinkOut, status_code=201)
def add_link(body: LinkIn, identity: Identity) -> LinkOut:
    """Cross-link a reference to a matter with a citation state (idempotent on reference+matter)."""
    try:
        with identity.connection() as conn:
            link_id = link_reference(
                conn, body.reference_id, body.matter_id, identity.entra.os_user_id,
                citation_state=body.citation_state, ids_bundle=body.ids_bundle, notes=body.notes,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Matter not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    return LinkOut(id=link_id)


@router.patch("/links/{link_id}", response_model=LinkOut)
def update_link_state(link_id: UUID, body: StateIn, identity: Identity) -> LinkOut:
    """Advance a citation's §1.56 state (to_disclose → disclosed → considered, …)."""
    try:
        with identity.connection() as conn:
            found = set_citation_state(conn, link_id, body.citation_state)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Reference link not found")
    return LinkOut(id=link_id)


@router.get("/citations", response_model=list[CitationRow])
def list_citations(
    identity: Identity, matter_id: UUID | None = None, family_id: UUID | None = None,
    citation_state: CitationState | None = None,
) -> list[CitationRow]:
    """The citations view (feeds the 4A.2 matrix + duty dashboard). RLS-scoped by matter."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select l.id, r.id, r.citation, r.kind::text, r.title, l.matter_id, m.reference,
                   l.family_id, l.citation_state::text, l.ids_bundle, l.linked_at
              from app.reference_links l
              join app.prior_art_references r on r.id = l.reference_id
              join app.matters m on m.id = l.matter_id
             where (%(matter_id)s::uuid is null or l.matter_id = %(matter_id)s)
               and (%(family_id)s::uuid is null or l.family_id = %(family_id)s)
               and (%(state)s::app.citation_state is null
                    or l.citation_state = %(state)s::app.citation_state)
             order by r.citation, m.reference
             limit 1000
            """,
            {"matter_id": matter_id, "family_id": family_id, "state": citation_state},
        ).fetchall()
    return [
        CitationRow(
            link_id=r[0], reference_id=r[1], citation=r[2], kind=r[3], title=r[4], matter_id=r[5],
            matter_reference=r[6], family_id=r[7], citation_state=r[8], ids_bundle=r[9],
            linked_at=r[10],
        )
        for r in rows
    ]
