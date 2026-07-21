"""A18 reminder-ladder endpoints (WP 6.12) — configure ladders, run the sweep, halt, decide.

Everything is RLS-scoped through the caller's own connection (D44): ladder configuration is
permissions-admin territory, live schedules follow matter visibility. There is deliberately no
"send" endpoint — a reminder leaves the system only by approving its proposal in the WP 6.1 audit
queue (`POST /api/v1/orchestrator/actions/{id}/approve`).
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import reminders as rem
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/reminders", tags=["reminders"])


class RungIn(BaseModel):
    step_no: int
    offset_days: int
    label: str
    subject: str
    body: str


class LadderIn(BaseModel):
    kind: str                       # 'deadline' | 'awaiting_client'
    name: str
    task_type: str
    rungs: list[RungIn]
    jurisdiction_code: str | None = None
    rights_preserving: bool = False


class LadderOut(BaseModel):
    id: UUID
    kind: str
    name: str
    task_type: str
    jurisdiction_code: str | None
    rights_preserving: bool


class ScheduleOut(BaseModel):
    id: UUID
    ladder_id: UUID
    matter_id: UUID
    task_id: UUID | None
    work_item_id: UUID | None
    anchor_date: date
    status: str
    halted_reason: str | None


class SendOut(BaseModel):
    id: UUID
    step_no: int
    due_on: date
    status: str
    subject: str
    review_required: bool
    suppressed_reason: str | None
    sent_at: datetime | None
    delivery_status: str | None


class HaltIn(BaseModel):
    reason: str


class SweepOut(BaseModel):
    queued: int
    suppressed: int
    superseded: int
    exhausted: int
    escalated: int
    cancelled: int


class EscalationOut(BaseModel):
    id: UUID
    schedule_id: UUID
    escalated_to: UUID | None
    created_at: datetime
    matter_reference: str
    rights_preserving: bool
    ladder_name: str


class DecisionIn(BaseModel):
    decision: str                   # 'pay' | 'abandon' | 'other'
    note: str | None = None


@router.post("/ladders", response_model=LadderOut, status_code=201)
def create_ladder(body: LadderIn, identity: Identity) -> LadderOut:
    """Define a ladder. Admin-gated by RLS, and shape-validated before anything is stored."""
    with identity.connection() as conn:
        try:
            ladder_id = rem.save_ladder(
                conn, body.kind, body.name, body.task_type,
                [rem.Rung(**r.model_dump()) for r in body.rungs],
                created_by=UUID(identity.entra.os_user_id),
                jurisdiction_code=body.jurisdiction_code,
                rights_preserving=body.rights_preserving,
            )
        except rem.LadderConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LadderOut(
        id=ladder_id, kind=body.kind, name=body.name, task_type=body.task_type,
        jurisdiction_code=body.jurisdiction_code, rights_preserving=body.rights_preserving,
    )


@router.get("/ladders", response_model=list[LadderOut])
def list_ladders(identity: Identity) -> list[LadderOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            "select id, kind::text, name, task_type, jurisdiction_code, rights_preserving "
            " from app.reminder_ladders where active order by task_type, jurisdiction_code nulls "
            " last",
        ).fetchall()
    return [
        LadderOut(id=r[0], kind=r[1], name=r[2], task_type=r[3], jurisdiction_code=r[4],
                  rights_preserving=r[5])
        for r in rows
    ]


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(identity: Identity, matter_id: UUID | None = None) -> list[ScheduleOut]:
    """Live and historical ladders, optionally for one matter. RLS hides the rest."""
    sql = (
        "select id, ladder_id, matter_id, task_id, work_item_id, anchor_date, status::text, "
        " halted_reason from app.reminder_schedules"
    )
    params: tuple[object, ...] = ()
    if matter_id is not None:
        sql += " where matter_id = %s"
        params = (matter_id,)
    sql += " order by created_at desc"
    with identity.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        ScheduleOut(id=r[0], ladder_id=r[1], matter_id=r[2], task_id=r[3], work_item_id=r[4],
                    anchor_date=r[5], status=r[6], halted_reason=r[7])
        for r in rows
    ]


@router.get("/schedules/{schedule_id}/sends", response_model=list[SendOut])
def list_sends(schedule_id: UUID, identity: Identity) -> list[SendOut]:
    """Every rung of one ladder, including the ones withheld and why."""
    with identity.connection() as conn:
        rows = conn.execute(
            "select id, step_no, due_on, status::text, subject, review_required, "
            " suppressed_reason, sent_at, delivery_status from app.reminder_sends "
            " where schedule_id = %s order by step_no",
            (schedule_id,),
        ).fetchall()
    return [
        SendOut(id=r[0], step_no=r[1], due_on=r[2], status=r[3], subject=r[4],
                review_required=r[5], suppressed_reason=r[6], sent_at=r[7], delivery_status=r[8])
        for r in rows
    ]


@router.post("/schedules/{schedule_id}/halt", status_code=204)
def halt(schedule_id: UUID, body: HaltIn, identity: Identity) -> None:
    """Log that an instruction arrived. The manual counterpart to A8's reply detection."""
    with identity.connection() as conn:
        try:
            rem.halt_schedule(conn, schedule_id, body.reason)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sweep", response_model=SweepOut)
def sweep(identity: Identity, as_of: date | None = None) -> SweepOut:
    """Advance every visible active schedule one tick. Idempotent; `as_of` is for dry-running."""
    with identity.connection() as conn:
        result = rem.sweep_reminders(conn, today=as_of)
    return SweepOut(**result.__dict__)


@router.get("/escalations", response_model=list[EscalationOut])
def escalations(identity: Identity) -> list[EscalationOut]:
    """Exhausted ladders awaiting an explicit decision — rights-preserving ones first."""
    with identity.connection() as conn:
        rows = rem.pending_escalations(conn)
    return [EscalationOut(**r) for r in rows]  # type: ignore[arg-type]


@router.post("/escalations/{escalation_id}/decide", status_code=204)
def decide(escalation_id: UUID, body: DecisionIn, identity: Identity) -> None:
    with identity.connection() as conn:
        try:
            rem.record_decision(
                conn, escalation_id, body.decision, UUID(identity.entra.os_user_id), body.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
