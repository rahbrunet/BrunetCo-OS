"""Docketing engine endpoints (WP 1.2): fire triggers, complete tasks, query provenance.

RLS-scoped throughout (D44): the engine runs on the caller's connection, so a matter the caller
cannot see is a 404 and RLS-denied writes surface as 403 — no app-layer permission checks.

Rule authoring/editing is deliberately NOT here — that is the WP 1.3 approval-gated editor
(M1-R4). Rules land via migration/seed/import until then.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from py_shared.domain.docketing import GeneratedTask, complete_task, fire_trigger
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/docket", tags=["docketing"])

TriggerType = Literal["event", "task_completion", "field_change", "watcher", "manual"]


class TriggerRequest(BaseModel):
    matter_id: UUID
    trigger_code: str
    ref_date: date
    trigger_type: TriggerType = "manual"  # staff-fired by default; watchers/agents set their own
    trigger_id: str | None = None


class GeneratedTaskOut(BaseModel):
    task_id: UUID
    provenance_id: UUID
    rule_id: UUID
    rule_version: int
    title: str
    respond_by: date | None
    final_due_date: date | None


class CompleteRequest(BaseModel):
    closed_on: date


class CompleteResponse(BaseModel):
    status: str
    chained: list[GeneratedTaskOut]


TaskStatus = Literal["open", "completed", "received", "not_needed", "missed"]
DeadlineType = Literal[
    "hard_external", "extendable_external", "internal", "general_reminder", "event",
    "transient_event",
]


class DocketTaskOut(BaseModel):
    """A docket-view row: the task plus the matter/family context views need (M1-R5)."""

    id: UUID
    matter_id: UUID
    matter_reference: str
    family_id: UUID
    title: str
    deadline_type: DeadlineType
    status: TaskStatus
    ref_date: date | None
    respond_by: date | None
    final_due_date: date | None
    closed_on: date | None
    assignee_id: UUID | None
    rule_id: UUID | None
    rule_version: int | None
    created_at: datetime


class ProvenanceOut(BaseModel):
    id: UUID
    task_id: UUID
    matter_id: UUID
    family_id: UUID
    rule_id: UUID
    rule_version: int
    trigger_type: TriggerType
    trigger_id: str | None
    input_dates: dict[str, Any]
    calculated_dates: dict[str, Any]
    generated_by: str
    generated_at: datetime


def _out(tasks: list[GeneratedTask]) -> list[GeneratedTaskOut]:
    return [GeneratedTaskOut(**t.__dict__) for t in tasks]


@router.post("/trigger", response_model=list[GeneratedTaskOut], status_code=201)
def trigger(body: TriggerRequest, identity: Identity) -> list[GeneratedTaskOut]:
    try:
        with identity.connection() as conn:
            tasks = fire_trigger(
                conn, body.matter_id, body.trigger_code, body.ref_date,
                trigger_type=body.trigger_type, trigger_id=body.trigger_id,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Matter not found") from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    return _out(tasks)


@router.post("/tasks/{task_id}/complete", response_model=CompleteResponse)
def complete(task_id: UUID, body: CompleteRequest, identity: Identity) -> CompleteResponse:
    try:
        with identity.connection() as conn:
            found, chained = complete_task(conn, task_id, body.closed_on)
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if not found:
        raise HTTPException(status_code=404, detail="Open task not found")
    return CompleteResponse(status="completed", chained=_out(chained))


@router.get("/tasks", response_model=list[DocketTaskOut])
def docket_tasks(
    identity: Identity,
    matter_id: UUID | None = None,
    family_id: UUID | None = None,
    assignee_id: UUID | None = None,
    status: TaskStatus | None = "open",
    due_from: date | None = None,
    due_to: date | None = None,
    overdue: bool = False,
) -> list[DocketTaskOut]:
    """Docket views (M1-R5): the open docket, filterable by matter/family/assignee/date window.

    ``status`` defaults to open (pass explicitly for closed history); ``overdue=true`` restricts
    to tasks whose earliest live date (respond_by, else final_due_date) is already past.
    RLS scopes rows to matters the caller can see — restricted families simply don't appear.
    """
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select t.id, t.matter_id, m.reference, m.family_id, t.title,
                   t.deadline_type::text, t.status::text, t.ref_date, t.respond_by,
                   t.final_due_date, t.closed_on, t.assignee_id, t.rule_id, t.rule_version,
                   t.created_at
              from app.tasks t join app.matters m on m.id = t.matter_id
             where (%(matter_id)s::uuid is null or t.matter_id = %(matter_id)s)
               and (%(family_id)s::uuid is null or m.family_id = %(family_id)s)
               and (%(assignee_id)s::uuid is null or t.assignee_id = %(assignee_id)s)
               and (%(status)s::app.task_status is null
                    or t.status = %(status)s::app.task_status)
               and (%(due_from)s::date is null
                    or coalesce(t.respond_by, t.final_due_date) >= %(due_from)s)
               and (%(due_to)s::date is null
                    or coalesce(t.respond_by, t.final_due_date) <= %(due_to)s)
               and (not %(overdue)s
                    or coalesce(t.respond_by, t.final_due_date) < current_date)
             order by coalesce(t.respond_by, t.final_due_date) nulls last, t.created_at
             limit 1000
            """,
            {"matter_id": matter_id, "family_id": family_id, "assignee_id": assignee_id,
             "status": status, "due_from": due_from, "due_to": due_to, "overdue": overdue},
        ).fetchall()
    return [
        DocketTaskOut(
            id=r[0], matter_id=r[1], matter_reference=r[2], family_id=r[3], title=r[4],
            deadline_type=r[5], status=r[6], ref_date=r[7], respond_by=r[8],
            final_due_date=r[9], closed_on=r[10], assignee_id=r[11], rule_id=r[12],
            rule_version=r[13], created_at=r[14],
        )
        for r in rows
    ]


_PROVENANCE_SQL = """
select id, task_id, matter_id, family_id, rule_id, rule_version, trigger_type::text,
       trigger_id, input_dates, calculated_dates, generated_by::text, generated_at
  from app.task_provenance
 where (%(matter_id)s::uuid is null or matter_id = %(matter_id)s)
   and (%(rule_id)s::uuid is null or rule_id = %(rule_id)s)
   and (%(trigger_type)s::app.trigger_type is null
        or trigger_type = %(trigger_type)s::app.trigger_type)
   and (%(generated_from)s::timestamptz is null or generated_at >= %(generated_from)s)
   and (%(generated_to)s::timestamptz is null or generated_at < %(generated_to)s)
 order by generated_at desc
 limit 1000
"""


def _provenance_rows(
    identity: Identity, matter_id: UUID | None, rule_id: UUID | None,
    trigger_type: TriggerType | None, generated_from: datetime | None,
    generated_to: datetime | None,
) -> list[tuple[Any, ...]]:
    params = {
        "matter_id": matter_id, "rule_id": rule_id, "trigger_type": trigger_type,
        "generated_from": generated_from, "generated_to": generated_to,
    }
    with identity.connection() as conn:
        return conn.execute(_PROVENANCE_SQL, params).fetchall()


@router.get("/provenance", response_model=list[ProvenanceOut])
def provenance(
    identity: Identity,
    matter_id: UUID | None = None,
    rule_id: UUID | None = None,
    trigger_type: TriggerType | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
) -> list[ProvenanceOut]:
    """M1-R14 provenance search — filterable by matter, rule, trigger type, date range."""
    rows = _provenance_rows(
        identity, matter_id, rule_id, trigger_type, generated_from, generated_to
    )
    return [
        ProvenanceOut(
            id=r[0], task_id=r[1], matter_id=r[2], family_id=r[3], rule_id=r[4],
            rule_version=r[5], trigger_type=r[6], trigger_id=r[7], input_dates=r[8],
            calculated_dates=r[9], generated_by=r[10], generated_at=r[11],
        )
        for r in rows
    ]


@router.get("/provenance.csv")
def provenance_csv(
    identity: Identity,
    matter_id: UUID | None = None,
    rule_id: UUID | None = None,
    trigger_type: TriggerType | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
) -> Response:
    """CSV export of the provenance log (M1-R14) — the WP 1.7 parallel-run diff input."""
    rows = _provenance_rows(
        identity, matter_id, rule_id, trigger_type, generated_from, generated_to
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "task_id", "matter_id", "family_id", "rule_id", "rule_version", "trigger_type",
        "trigger_id", "input_dates", "calculated_dates", "generated_by", "generated_at",
    ])
    for r in rows:
        writer.writerow([str(v) if v is not None else "" for v in r])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=task-provenance.csv"},
    )
