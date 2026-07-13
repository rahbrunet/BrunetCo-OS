"""Conflict-check endpoints (WP 5B.4, D34/D38).

Intake and on-demand conflict searches, each logged (D38: no matter opens without a conflicts
check, and a cleared conflict must be provable). The search sees the whole firm via the definer
function; the check log is firm-general and RLS-scoped to active staff.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from py_shared.domain.conflicts import run_and_log_check
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/conflicts", tags=["conflicts"])


class CheckRequest(BaseModel):
    query: str
    check_type: Literal["intake", "on_demand"] = "on_demand"
    matter_id: UUID | None = None
    min_score: float = 0.3


class MatchOut(BaseModel):
    kind: str
    ref: str
    label: str
    matched_on: str
    score: float


class CheckResult(BaseModel):
    check_id: UUID
    query: str
    result_count: int
    matches: list[MatchOut]


class CheckLogOut(BaseModel):
    id: UUID
    query_text: str
    check_type: str
    matter_id: UUID | None
    result_count: int
    results: list[dict[str, Any]]
    cleared: bool
    run_at: datetime


@router.post("/check", response_model=CheckResult)
def run_check(body: CheckRequest, identity: Identity) -> CheckResult:
    """Run a conflict check and log it. Returns the ranked matches; an empty list means clear."""
    try:
        with identity.connection() as conn:
            check_id, matches = run_and_log_check(
                conn, body.query, identity.entra.os_user_id,
                check_type=body.check_type, matter_id=body.matter_id, min_score=body.min_score,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    return CheckResult(
        check_id=check_id, query=body.query, result_count=len(matches),
        matches=[MatchOut(**m.as_json()) for m in matches],
    )


@router.get("", response_model=list[CheckLogOut])
def list_checks(
    identity: Identity, matter_id: UUID | None = None, cleared: bool | None = None,
) -> list[CheckLogOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, query_text, check_type::text, matter_id, result_count, results, cleared,
                   run_at
              from app.conflict_checks
             where (%(matter_id)s::uuid is null or matter_id = %(matter_id)s)
               and (%(cleared)s::boolean is null or cleared = %(cleared)s)
             order by run_at desc
             limit 500
            """,
            {"matter_id": matter_id, "cleared": cleared},
        ).fetchall()
    return [
        CheckLogOut(id=r[0], query_text=r[1], check_type=r[2], matter_id=r[3], result_count=r[4],
                    results=r[5], cleared=r[6], run_at=r[7])
        for r in rows
    ]


@router.post("/{check_id}/clear", response_model=CheckLogOut)
def clear_check(check_id: UUID, identity: Identity) -> CheckLogOut:
    """Mark a logged check as cleared (a human judged no true conflict)."""
    try:
        with identity.connection() as conn:
            row = conn.execute(
                """
                update app.conflict_checks
                   set cleared = true, cleared_by = %s, cleared_at = now()
                 where id = %s
                returning id, query_text, check_type::text, matter_id, result_count, results,
                          cleared, run_at
                """,
                (identity.entra.os_user_id, check_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Conflict check not found")
    return CheckLogOut(id=row[0], query_text=row[1], check_type=row[2], matter_id=row[3],
                       result_count=row[4], results=row[5], cleared=row[6], run_at=row[7])
