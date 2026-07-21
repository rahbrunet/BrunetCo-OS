"""EOS endpoints (WP 5.7) — scorecard, issues (IDS), to-dos, rocks.

EOS is run in the open, so these are staff reads and writes; the accountability discipline lives
in the owner column and the L10 process, not in per-row locks. The interesting endpoints are the
IDS transition (which enforces the lifecycle) and the scorecard read (which carries the RAG
colour the whole firm reviews together).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import eos
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/eos", tags=["eos"])


class ScorecardRow(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    goal: float
    unit: str | None
    value: float | None
    rag: str


class IssueTransitionIn(BaseModel):
    to_status: str
    resolution: str | None = None


class TodoScoreOut(BaseModel):
    committed: int
    done: int
    rate: float
    meets_target: bool


@router.get("/scorecard", response_model=list[ScorecardRow])
def scorecard(identity: Identity) -> list[ScorecardRow]:
    """The current scorecard with each measurable's latest value and RAG colour."""
    with identity.connection() as conn:
        rows = conn.execute(
            "select id, name, owner_id, goal, unit, value, rag from app.scorecard_current "
            " order by name"
        ).fetchall()
    return [
        ScorecardRow(id=r[0], name=r[1], owner_id=r[2], goal=float(r[3]), unit=r[4],
                     value=float(r[5]) if r[5] is not None else None, rag=r[6])
        for r in rows
    ]


@router.post("/issues/{issue_id}/advance", status_code=204)
def advance_issue(issue_id: UUID, body: IssueTransitionIn, identity: Identity) -> None:
    """Move an issue through Identify → Discuss → Solve (or drop it). Enforces the lifecycle."""
    with identity.connection() as conn:
        try:
            eos.advance_issue(conn, issue_id, body.to_status, body.resolution)
        except eos.IssueTransitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/todos/{todo_id}/complete", status_code=204)
def complete_todo(todo_id: UUID, identity: Identity) -> None:
    with identity.connection() as conn:
        eos.complete_todo(conn, todo_id)


@router.get("/todos/score", response_model=TodoScoreOut)
def todo_score(identity: Identity) -> TodoScoreOut:
    """The caller's own To-Do completion against the EOS 90% target."""
    with identity.connection() as conn:
        score = eos.todo_score(conn, UUID(identity.entra.os_user_id))
    return TodoScoreOut(committed=score.committed, done=score.done, rate=score.rate,
                        meets_target=score.meets_target)
