"""Unscripted-project endpoints (WP 5.6) — draft a plan from NL, then launch it.

Two steps, deliberately separate: `draft` returns an editable plan (nothing is created), and
`launch` instantiates a plan the user has settled on. The gap between them is where the human edits
— the whole point of the feature.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import project_planner as pp
from py_shared.domain import projects as pj
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class PlanTaskIO(BaseModel):
    task_ref: str
    title: str
    role: str | None = None
    cycle_days: int = 1
    start_offset_days: int = 0
    stage: str | None = None


class PlanEdgeIO(BaseModel):
    task_ref: str
    depends_on_ref: str


class DraftIn(BaseModel):
    description: str


class PlanOut(BaseModel):
    tasks: list[PlanTaskIO]
    edges: list[PlanEdgeIO]


class LaunchIn(BaseModel):
    name: str
    tasks: list[PlanTaskIO]
    edges: list[PlanEdgeIO]
    start: date
    matter_id: UUID | None = None
    family_id: UUID | None = None


class LaunchOut(BaseModel):
    project_id: UUID


def _to_plan(
    tasks: list[PlanTaskIO], edges: list[PlanEdgeIO],
) -> tuple[list[pj.TemplateTask], list[pj.TemplateEdge]]:
    return (
        [pj.TemplateTask(task_ref=t.task_ref, title=t.title, role=t.role,
                         cycle_days=t.cycle_days, start_offset_days=t.start_offset_days,
                         stage=t.stage, ordinal=i) for i, t in enumerate(tasks)],
        [pj.TemplateEdge(task_ref=e.task_ref, depends_on_ref=e.depends_on_ref) for e in edges],
    )


@router.post("/draft-plan", response_model=PlanOut)
def draft_plan(body: DraftIn, identity: Identity) -> PlanOut:
    """Draft an editable plan from a description. Creates nothing; the user edits, then launches."""
    with identity.connection() as conn:
        try:
            tasks, edges = pp.draft_plan(conn, body.description)
        except pp.PlanningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PlanOut(
        tasks=[PlanTaskIO(task_ref=t.task_ref, title=t.title, role=t.role,
                          cycle_days=t.cycle_days, start_offset_days=t.start_offset_days,
                          stage=t.stage) for t in tasks],
        edges=[PlanEdgeIO(task_ref=e.task_ref, depends_on_ref=e.depends_on_ref) for e in edges],
    )


@router.post("/launch-adhoc", response_model=LaunchOut, status_code=201)
def launch_adhoc(body: LaunchIn, identity: Identity) -> LaunchOut:
    """Launch an edited plan into a real chained project."""
    tasks, edges = _to_plan(body.tasks, body.edges)
    with identity.connection() as conn:
        try:
            pid = pj.launch_adhoc_project(
                conn, body.name, tasks, edges, UUID(identity.entra.os_user_id), body.start,
                matter_id=body.matter_id, family_id=body.family_id,
            )
        except pj.TemplateInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LaunchOut(project_id=pid)
