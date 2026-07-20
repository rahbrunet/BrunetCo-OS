"""Assignment + capacity board endpoints (WP 5.2, spec §M9).

The capacity board reads `app.capacity_board`; assignment suggestions and reassignment run through
the domain engine. All on the caller's RLS-scoped connection (D44) — reassignment is a floor-level
action confined to items the caller can see, not an admin power.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import assignment
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/capacity", tags=["capacity"])


class CapacityRow(BaseModel):
    user_id: UUID
    display_name: str
    open_load: int
    in_progress: int
    overdue: int
    due_soon: int
    next_due: date | None
    roles: list[str]


class SuggestionOut(BaseModel):
    user_id: UUID
    display_name: str
    open_load: int
    at_capacity: bool
    avg_cycle_days: float | None


class ReassignIn(BaseModel):
    to_user: UUID | None
    reason: str | None = None


class EscalationOut(BaseModel):
    id: UUID
    work_item_id: UUID
    escalated_to: UUID | None
    reason: str
    due_date: date | None
    created_at: datetime
    resolved: bool


@router.get("/board", response_model=list[CapacityRow])
def capacity_board(identity: Identity) -> list[CapacityRow]:
    """One row per active person with their live load — the board that replaces the Word lists."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select user_id, display_name, open_load, in_progress, overdue, due_soon, next_due,
                   roles
              from app.capacity_board
             order by open_load desc, display_name
            """
        ).fetchall()
    return [
        CapacityRow(user_id=r[0], display_name=r[1], open_load=r[2], in_progress=r[3],
                    overdue=r[4], due_soon=r[5], next_due=r[6], roles=list(r[7]))
        for r in rows
    ]


@router.get("/suggest", response_model=list[SuggestionOut])
def suggest(
    identity: Identity, role: str, task_ref: str | None = None, limit: int = 5,
) -> list[SuggestionOut]:
    """Ranked assignment suggestions for a role, optionally for a specific task type."""
    with identity.connection() as conn:
        candidates = assignment.suggest_assignees(conn, role, task_ref, min(limit, 20))
    return [
        SuggestionOut(user_id=c.user_id, display_name=c.display_name, open_load=c.open_load,
                      at_capacity=c.at_capacity, avg_cycle_days=c.avg_cycle_days)
        for c in candidates
    ]


@router.post("/work-items/{item_id}/reassign", status_code=204)
def reassign(item_id: UUID, body: ReassignIn, identity: Identity) -> None:
    """Drag-to-reassign, logged. A move to an item the caller cannot see is a 404 (RLS renders it
    indistinguishable from absent), so the endpoint cannot probe for hidden items."""
    with identity.connection() as conn:
        try:
            assignment.reassign_work_item(
                conn, item_id, body.to_user, UUID(identity.entra.os_user_id), body.reason
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/escalations", response_model=list[EscalationOut])
def list_escalations(identity: Identity, open_only: bool = True) -> list[EscalationOut]:
    """SLA-breach escalations visible to the caller (by matter visibility)."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, work_item_id, escalated_to, reason, due_date, created_at, resolved
              from app.escalations
             where (%(open_only)s = false or resolved_at is null)
             order by created_at desc
             limit 200
            """,
            {"open_only": open_only},
        ).fetchall()
    return [
        EscalationOut(id=r[0], work_item_id=r[1], escalated_to=r[2], reason=r[3], due_date=r[4],
                      created_at=r[5], resolved=r[6])
        for r in rows
    ]


@router.post("/escalations/{escalation_id}/resolve", status_code=204)
def resolve_escalation(escalation_id: UUID, identity: Identity) -> None:
    """Close an escalation once the underlying breach is handled."""
    with identity.connection() as conn:
        try:
            assignment.resolve_escalation(conn, escalation_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
