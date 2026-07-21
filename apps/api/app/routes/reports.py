"""Report Builder endpoints (WP 5B.1) â€” datasets, saved reports, runs, schedule sweep.

Every call runs on the caller's own RLS-scoped connection (D44), which is what makes sharing safe:
the same shared report run by two people returns each person's permitted rows.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import reports as rp
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class AggregateIn(BaseModel):
    func: str
    column: str | None = None


class DefinitionIn(BaseModel):
    dataset: str
    columns: list[str] = []
    filters: list[dict[str, Any]] = []
    group_by: list[str] = []
    aggregates: list[AggregateIn] = []
    sort: list[str] = []
    limit: int = 1000

    def to_domain(self) -> rp.Definition:
        return rp.Definition(
            dataset=self.dataset, columns=self.columns, filters=self.filters,
            group_by=self.group_by, sort=self.sort, limit=self.limit,
            aggregates=[rp.Aggregate(a.func, a.column) for a in self.aggregates],
        )


class ReportIn(BaseModel):
    name: str
    definition: DefinitionIn
    shared: bool = False
    schedule_frequency: str | None = None
    schedule_hour: int = 7


class ReportOut(BaseModel):
    id: UUID
    name: str
    dataset_key: str
    owner_id: UUID
    shared: bool
    schedule_frequency: str | None
    schedule_hour: int
    active: bool


class RunOut(BaseModel):
    run_id: UUID
    columns: list[str]
    row_count: int
    rows: list[dict[str, Any]]


class RunLogOut(BaseModel):
    id: UUID
    run_at: datetime
    row_count: int
    status: str
    error: str | None


@router.get("/datasets")
def datasets() -> list[dict[str, Any]]:
    """What a report may query. There is nothing outside this registry."""
    return rp.describe_datasets()


@router.post("", response_model=ReportOut, status_code=201)
def create(body: ReportIn, identity: Identity) -> ReportOut:
    with identity.connection() as conn:
        try:
            rid = rp.save_report(
                conn, UUID(identity.entra.os_user_id), body.name, body.definition.to_domain(),
                shared=body.shared, schedule_frequency=body.schedule_frequency,
                schedule_hour=body.schedule_hour,
            )
        except rp.ReportDefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rows = [r for r in rp.list_reports(conn) if r["id"] == rid]
    return ReportOut(**rows[0])


@router.get("", response_model=list[ReportOut])
def index(identity: Identity) -> list[ReportOut]:
    """The caller's own reports plus anything shared with the firm."""
    with identity.connection() as conn:
        rows = rp.list_reports(conn)
    return [ReportOut(**r) for r in rows]


@router.post("/{report_id}/run", response_model=RunOut)
def run(report_id: UUID, identity: Identity) -> RunOut:
    with identity.connection() as conn:
        try:
            result = rp.run_report(conn, report_id, UUID(identity.entra.os_user_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except rp.ReportDefinitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RunOut(
        run_id=result.run_id, columns=result.columns, row_count=result.row_count, rows=result.rows,
    )


@router.get("/{report_id}/runs", response_model=list[RunLogOut])
def runs(report_id: UUID, identity: Identity) -> list[RunLogOut]:
    with identity.connection() as conn:
        rows = rp.recent_runs(conn, report_id)
    return [RunLogOut(**r) for r in rows]


@router.get("/{report_id}/export")
def export(report_id: UUID, identity: Identity) -> dict[str, str]:
    """A run rendered as a spreadsheet. Email/SFTP delivery lands with WP 4.3."""
    with identity.connection() as conn:
        try:
            result = rp.run_report(conn, report_id, UUID(identity.entra.os_user_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"filename": f"report-{report_id}.csv", "content": rp.to_spreadsheet(result)}


@router.get("/scheduled/due", response_model=list[UUID])
def due(identity: Identity) -> list[UUID]:
    """Scheduled reports whose window has come â€” the runner's work list."""
    with identity.connection() as conn:
        return rp.due_reports(conn)
