"""Orchestrator project planner (WP 5.6, spec §M9, D30) — natural language to an editable plan.

"Describe the project in natural language → draft plan (stages, tasks, owners, dependencies,
target dates) → user edits → launch." The spec's bar is that creating a tracked project must be
*easier than opening Word*, so the flow is: one sentence in, a structured plan out, which the
human then edits and launches via `projects.launch_adhoc_project`.

Two things make this safe rather than a magic box:

  * **The description is redacted before it reaches the LLM** (D45), exactly like A9 drafting — a
    project description ("assignment recordal for Acme across US/EP/CA") carries client identity,
    and the planner is not exempt from the egress gate. Task titles are rehydrated after.

  * **The model's plan is validated before it is ever shown**, with the same rules a template
    faces. An LLM will occasionally emit a dependency cycle or a dangling reference; catching that
    here means the user edits a sound plan, never launches a broken one.

The planner DRAFTS; it does not launch. Nothing becomes real work until a human has seen the plan
and pressed go — the invariant every agent in this system shares.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg

from py_shared import llm, redaction
from py_shared.domain.projects import TemplateEdge, TemplateTask, validate_template
from py_shared.orchestrator import LLM_EGRESS, egress_check

AGENT_NAME = "a0-planner"
TASK = "plan_project"

SYSTEM_PROMPT = (
    "You are helping an intellectual-property practice turn a short description of a project into "
    "a structured plan. Reply with ONLY a JSON object of the form:\n"
    '{"tasks": [{"ref": "slug", "title": "...", "role": "agent|paralegal|principal|bookkeeper", '
    '"cycle_days": <int business days>, "stage": "..."}], '
    '"edges": [{"task": "slug", "depends_on": "slug"}]}\n'
    "Use short lowercase slugs for refs. cycle_days is working days for that step. Express real "
    "ordering as edges (a task depends_on the tasks that must finish first). Placeholder tokens "
    "such as PERSON_001 or ORG_002 in the description must be reproduced verbatim if referenced; "
    "never invent identifiers. Do not include any prose outside the JSON."
)


class PlanningError(ValueError):
    """The model's response could not be turned into a valid plan."""


def _parse_plan(raw: str) -> tuple[list[TemplateTask], list[TemplateEdge]]:
    """Turn the model's JSON into typed tasks and edges, defensively.

    An LLM is a best-effort source: it may wrap the JSON in prose, omit fields, or mistype a
    number. Each of those is a clear PlanningError rather than a stack trace, because this text is
    user-facing — "the planner returned something malformed, try rephrasing" is a usable message.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise PlanningError("no JSON object found in the planner response")
    try:
        data: dict[str, Any] = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise PlanningError(f"planner response was not valid JSON: {exc}") from exc

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanningError("planner response has no tasks")

    tasks: list[TemplateTask] = []
    for entry in raw_tasks:
        if not isinstance(entry, dict) or not entry.get("ref") or not entry.get("title"):
            raise PlanningError(f"malformed task entry: {entry!r}")
        try:
            cycle = int(entry.get("cycle_days", 1))
        except (TypeError, ValueError) as exc:
            raise PlanningError(f"task {entry['ref']!r} has a non-integer cycle_days") from exc
        tasks.append(TemplateTask(
            task_ref=str(entry["ref"]),
            title=str(entry["title"]),
            role=str(entry["role"]) if entry.get("role") else None,
            cycle_days=max(cycle, 0),
            stage=str(entry["stage"]) if entry.get("stage") else None,
            ordinal=len(tasks),
        ))

    edges: list[TemplateEdge] = []
    for entry in data.get("edges", []) or []:
        if not isinstance(entry, dict) or not entry.get("task") or not entry.get("depends_on"):
            raise PlanningError(f"malformed edge entry: {entry!r}")
        edges.append(TemplateEdge(task_ref=str(entry["task"]),
                                  depends_on_ref=str(entry["depends_on"])))
    return tasks, edges


def _rehydrate_plan(
    tasks: list[TemplateTask], mapping: dict[str, str],
) -> list[TemplateTask]:
    """Restore real identities in task titles/stages after the redacted round-trip."""
    return [
        TemplateTask(
            task_ref=t.task_ref,
            title=redaction.rehydrate(t.title, mapping),
            role=t.role,
            cycle_days=t.cycle_days,
            start_offset_days=t.start_offset_days,
            stage=redaction.rehydrate(t.stage, mapping) if t.stage else None,
            is_milestone=t.is_milestone,
            ordinal=t.ordinal,
        )
        for t in tasks
    ]


def draft_plan(
    conn: psycopg.Connection,
    description: str,
    llm_client: llm.LlmClient | None = None,
    ner_backend: redaction.NerBackend | None = None,
) -> tuple[list[TemplateTask], list[TemplateEdge]]:
    """Draft an editable project plan from a natural-language description.

    Redacts → gate → LLM → parse → rehydrate → validate. Returns tasks and edges for the user to
    edit; it does NOT launch. A plan that fails validation raises PlanningError rather than
    returning something the user could unknowingly launch broken.
    """
    if not description.strip():
        raise PlanningError("describe the project in a sentence or two")

    result = redaction.redact_for_egress(conn, AGENT_NAME, description, backend=ner_backend)
    egress_check(LLM_EGRESS, result.ref)

    client = llm_client or llm.get_llm_client(TASK)
    try:
        raw = client.complete(result.masked, system=SYSTEM_PROMPT)
    except llm.LlmError as exc:
        llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked),
                       status="failed", detail=str(exc)[:500])
        raise
    llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked))

    tasks, edges = _parse_plan(raw)
    tasks = _rehydrate_plan(tasks, result.mapping)
    try:
        validate_template(tasks, edges)
    except Exception as exc:  # ValueError family from validate_template
        raise PlanningError(f"the drafted plan was not sound: {exc}") from exc
    return tasks, edges
