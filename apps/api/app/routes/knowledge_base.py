"""Knowledge Base endpoints (WP 6.8, spec §12.1) — citation-aware search + corpus health.

The KB carries no client data, so these reads are firm-general rather than RLS-scoped per user.
What the API does carry through is the licence posture: `may_quote` is returned on every passage,
because a consumer that receives text without the constraint attached will eventually reproduce
third-party material verbatim.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from py_shared.domain import knowledge_base as kb
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])

# Repeatable ?jurisdiction=CA&jurisdiction=US. Annotated form keeps the Query() call out of the
# argument default, which ruff flags (B008) because a shared mutable default is a real hazard.
JurisdictionFilter = Annotated[list[str] | None, Query()]


class PassageOut(BaseModel):
    chunk_id: UUID
    citation: str
    heading_path: str
    extract: str
    source_key: str
    source_name: str
    jurisdiction: str
    edition_label: str
    license_class: str
    # Surfaced explicitly: third-party passages are cited and paraphrased, never reproduced.
    may_quote: bool
    is_superseded: bool
    rank: float


class SourceOut(BaseModel):
    key: str
    name: str
    jurisdiction: str
    authority_type: str
    license_class: str
    edition_label: str
    refreshed_at: datetime | None
    refresh_interval_days: int | None
    is_superseded: bool
    is_stale: bool


@router.get("/search", response_model=list[PassageOut])
def search(
    identity: Identity,
    q: str,
    jurisdiction: JurisdictionFilter = None,
    limit: int = 8,
    include_superseded: bool = False,
) -> list[PassageOut]:
    """Ranked passages with citations.

    `include_superseded` defaults false — current law unless history is explicitly requested.
    """
    with identity.connection() as conn:
        passages = kb.search(conn, q, jurisdiction, min(limit, 50), include_superseded)
    return [
        PassageOut(
            chunk_id=p.chunk_id, citation=p.citation, heading_path=p.heading_path,
            extract=p.extract, source_key=p.source_key, source_name=p.source_name,
            jurisdiction=p.jurisdiction, edition_label=p.edition_label,
            license_class=p.license_class, may_quote=p.may_quote,
            is_superseded=p.is_superseded, rank=p.rank,
        )
        for p in passages
    ]


@router.get("/sources", response_model=list[SourceOut])
def list_sources(identity: Identity, stale_only: bool = False) -> list[SourceOut]:
    """The corpus registry with freshness state — the ops answer to "is our grounding current?".

    A source registered but never fetched reads as stale, which is the honest answer: an agent
    grounding on an empty feed is worse off than one that knows it has nothing.
    """
    today = date.today()
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select key, name, jurisdiction, authority_type::text, license_class::text,
                   edition_label, refreshed_at, refresh_interval_days, superseded_at
              from kb.sources
             where is_active
             order by jurisdiction, key
            """
        ).fetchall()

    out: list[SourceOut] = []
    for r in rows:
        source = kb.Source(
            key=r[0], name=r[1], jurisdiction=r[2], license_class=r[4], edition_label=r[5],
            refreshed_at=r[6].date() if r[6] else None, refresh_interval_days=r[7],
            superseded_at=r[8],
        )
        stale = kb.is_stale(source, today)
        if stale_only and not stale:
            continue
        out.append(SourceOut(
            key=r[0], name=r[1], jurisdiction=r[2], authority_type=r[3], license_class=r[4],
            edition_label=r[5], refreshed_at=r[6], refresh_interval_days=r[7],
            is_superseded=r[8] is not None, is_stale=stale,
        ))
    return out
