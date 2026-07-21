"""L10 pack + 1-on-1 report endpoints (WP 5.8, spec §M9).

The pack is generated on read rather than stored: it is a view of live data at a moment, and a
cached pack is a pack that disagrees with the board everyone is looking at during the meeting.

The 1-on-1 endpoint defaults to the caller's own report. Someone else's is reachable, because
supervision conversations are a real use, but the default being "yours" keeps the framing the spec
asks for — seat-owned measurables, not a surveillance console.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from py_shared.domain import eos_reporting as er
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/eos", tags=["eos"])


class L10PackOut(BaseModel):
    week_start: date
    quarter: str
    headline: dict[str, Any]
    scorecard: list[dict[str, Any]]
    rocks: dict[str, int]
    todo_completion: dict[str, Any]
    overdue_by_owner: list[dict[str, Any]]
    aging_wip: list[dict[str, Any]]
    critical_deadlines: list[dict[str, Any]]
    open_issues: list[dict[str, Any]]


class OneOnOneOut(BaseModel):
    user_id: UUID
    week_start: date
    measurables: list[dict[str, Any]]
    rocks: list[dict[str, Any]]
    todo_score: dict[str, Any]
    request_turnaround: dict[str, Any]
    open_items: list[dict[str, Any]]


class PopulateOut(BaseModel):
    written: int
    no_data: int


@router.get("/l10-pack", response_model=L10PackOut)
def l10_pack(quarter: str, identity: Identity) -> L10PackOut:
    """The weekly L10 pack, generated from live data."""
    with identity.connection() as conn:
        pack = er.l10_pack(conn, quarter)
    return L10PackOut(
        week_start=pack.week_start, quarter=pack.quarter, headline=pack.headline,
        scorecard=pack.scorecard, rocks=pack.rocks, todo_completion=pack.todo_completion,
        overdue_by_owner=pack.overdue_by_owner, aging_wip=pack.aging_wip,
        critical_deadlines=pack.critical_deadlines, open_issues=pack.open_issues,
    )


@router.get("/one-on-one", response_model=OneOnOneOut)
def one_on_one(quarter: str, identity: Identity, user_id: UUID | None = None) -> OneOnOneOut:
    """A person's 1-on-1 report. Defaults to the caller's own."""
    target = user_id or UUID(identity.entra.os_user_id)
    with identity.connection() as conn:
        report = er.one_on_one_report(conn, target, quarter)
    return OneOnOneOut(
        user_id=report.user_id, week_start=report.week_start, measurables=report.measurables,
        rocks=report.rocks, todo_score=report.todo_score,
        request_turnaround=report.request_turnaround, open_items=report.open_items,
    )


@router.post("/scorecard/populate", response_model=PopulateOut)
def populate(identity: Identity) -> PopulateOut:
    """Recompute every auto-populated measurable for the current week. Idempotent — safe to run
    nightly and again on demand before a meeting."""
    with identity.connection() as conn:
        result = er.populate_scorecard(conn)
    return PopulateOut(written=result["written"], no_data=result["no_data"])
