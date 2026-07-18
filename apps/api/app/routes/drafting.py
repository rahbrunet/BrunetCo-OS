"""A9 draft review queue (WP 6.9) — the human end of the drafter.

Every route runs on the caller's RLS-scoped connection (D44), and the drafts table is own-record
only (migration 0015), so "list my drafts" needs no user filter in SQL — Postgres already cannot
return anyone else's. The explicit ordering and status filter are the whole API surface: a queue
you read, approve, or decline.

There is deliberately no send endpoint. Approval marks a draft ready; sending is a separate human
action landing with the outbound-mail work (WP 4.5).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import drafting
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/drafts", tags=["drafting"])


class DraftOut(BaseModel):
    id: UUID
    in_reply_to: UUID | None
    matter_id: UUID | None
    subject: str | None
    body_text: str
    status: str
    provider: str | None
    model: str | None
    redaction_ref: str | None
    discard_reasons: list[str] | None
    created_at: datetime
    decided_at: datetime | None


class DecisionIn(BaseModel):
    approve: bool


class DecisionOut(BaseModel):
    id: UUID
    status: str


@router.get("", response_model=list[DraftOut])
def list_drafts(identity: Identity, status: str | None = "pending_review") -> list[DraftOut]:
    """The caller's own drafts, newest first. Defaults to the pending queue — the discarded ones
    are kept for audit (they are evidence the validator fired) but are not daily reading."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, in_reply_to, matter_id, subject, body_text, status::text, provider, model,
                   redaction_ref, discard_reasons, created_at, decided_at
              from app.email_drafts
             where (%(status)s::text is null or status::text = %(status)s)
             order by created_at desc
             limit 200
            """,
            {"status": status},
        ).fetchall()
    return [
        DraftOut(id=r[0], in_reply_to=r[1], matter_id=r[2], subject=r[3], body_text=r[4],
                 status=r[5], provider=r[6], model=r[7], redaction_ref=r[8], discard_reasons=r[9],
                 created_at=r[10], decided_at=r[11])
        for r in rows
    ]


@router.post("/{draft_id}/decision", response_model=DecisionOut)
def decide(draft_id: UUID, body: DecisionIn, identity: Identity) -> DecisionOut:
    """Approve or decline. A draft that is already decided (or belongs to someone else, which RLS
    renders indistinguishable from absent) is a 404 — the same answer either way, so the endpoint
    cannot be used to probe for other users' drafts."""
    with identity.connection() as conn:
        try:
            status = drafting.decide_draft(
                conn, draft_id, body.approve, UUID(identity.entra.os_user_id)
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DecisionOut(id=draft_id, status=status)


class RedactionEventOut(BaseModel):
    ref: str
    agent_name: str
    backend: str
    entity_counts: dict[str, int]
    structured_hits: int
    leaks: int
    created_at: datetime


@router.get("/redaction-events", response_model=list[RedactionEventOut])
def list_redaction_events(
    identity: Identity, agent_name: str | None = None,
) -> list[RedactionEventOut]:
    """D45 audit read: proof that redaction ran before each external call, and whether it came
    back clean. Counts only — the mapping is never persisted (migration 0015)."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select ref, agent_name, backend, entity_counts, structured_hits, leaks, created_at
              from ops.redaction_events
             where (%(agent)s::text is null or agent_name = %(agent)s)
             order by created_at desc
             limit 200
            """,
            {"agent": agent_name},
        ).fetchall()
    return [
        RedactionEventOut(ref=r[0], agent_name=r[1], backend=r[2], entity_counts=r[3],
                          structured_hits=r[4], leaks=r[5], created_at=r[6])
        for r in rows
    ]
