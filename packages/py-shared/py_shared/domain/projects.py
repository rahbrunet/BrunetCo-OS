"""Work-item engine + scripted project templates (WP 5.1, spec §M9, D30).

Tier 2 of the three-tier work model. A scripted project is a versioned template — stages, tasks,
role routing, standard cycle times and a dependency graph — instantiated against a matter into a
tree of work items with computed target dates.

The scheduling pass is the substantive part. Two decisions drive it:

  * **Cycle times are business days.** "Three days to review the draft" means three working days.
    Calendar arithmetic silently shortens every task that spans a weekend, and a project plan that
    quietly under-books time is worse than one with no dates at all.

  * **A task starts when its last predecessor finishes, not on a fixed offset.** The forward pass
    is the standard critical-path computation: earliest start = max(predecessor finishes). Fixed
    offsets alone would let a chained task be scheduled before the work it depends on.

Validation runs at publish, not at authoring. A half-built template legitimately has dangling
references and no edges yet; rejecting that would make templates impossible to author incrementally.
What must never happen is a *published* template with a cycle in it — that instantiates a project
whose tasks can never all complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

import psycopg

from py_shared.domain.deadlines import add_business_days

# ---------------------------------------------------------------------------
# Template model (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateTask:
    task_ref: str
    title: str
    role: str | None = None
    cycle_days: int = 1
    start_offset_days: int = 0
    stage: str | None = None
    is_milestone: bool = False
    ordinal: int = 0


@dataclass(frozen=True)
class TemplateEdge:
    """`task_ref` cannot start until `depends_on_ref` is done."""

    task_ref: str
    depends_on_ref: str


class TemplateInvalid(ValueError):
    """The template cannot be published. Carries every problem found, not just the first —
    an author fixing one dangling reference at a time is an author who stops using templates."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def validate_template(tasks: list[TemplateTask], edges: list[TemplateEdge]) -> None:
    """Raise `TemplateInvalid` unless this template is safe to publish."""
    problems: list[str] = []

    refs = [t.task_ref for t in tasks]
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            problems.append(f"duplicate task_ref {ref!r}")
        seen.add(ref)

    if not tasks:
        problems.append("template has no tasks")

    for edge in edges:
        if edge.task_ref not in seen:
            problems.append(f"dependency references unknown task {edge.task_ref!r}")
        if edge.depends_on_ref not in seen:
            problems.append(f"dependency references unknown task {edge.depends_on_ref!r}")
        if edge.task_ref == edge.depends_on_ref:
            problems.append(f"task {edge.task_ref!r} depends on itself")

    for task in tasks:
        if task.cycle_days < 0:
            problems.append(f"task {task.task_ref!r} has a negative cycle time")
        if task.start_offset_days < 0:
            problems.append(f"task {task.task_ref!r} has a negative start offset")

    # Only look for cycles once the edges are known to resolve; otherwise the traversal reports
    # confusing phantom cycles through references that simply do not exist.
    if not problems:
        cycle = _find_cycle(refs, edges)
        if cycle:
            problems.append("dependency cycle: " + " -> ".join(cycle))

    if problems:
        raise TemplateInvalid(problems)


def _find_cycle(refs: list[str], edges: list[TemplateEdge]) -> list[str] | None:
    """Return one cycle as a readable path, or None. Iterative DFS with an explicit stack — a
    recursive walk would blow the Python stack on a pathological template, and reporting the
    actual path is what lets an author find the loop."""
    successors: dict[str, list[str]] = {ref: [] for ref in refs}
    for edge in edges:
        successors[edge.depends_on_ref].append(edge.task_ref)

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(refs, WHITE)
    path: list[str] = []

    for start in refs:
        if colour[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path = [start]
        colour[start] = GREY
        while stack:
            node, index = stack[-1]
            if index < len(successors[node]):
                stack[-1] = (node, index + 1)
                nxt = successors[node][index]
                if colour[nxt] == GREY:
                    return path[path.index(nxt):] + [nxt]
                if colour[nxt] == WHITE:
                    colour[nxt] = GREY
                    stack.append((nxt, 0))
                    path.append(nxt)
            else:
                colour[node] = BLACK
                stack.pop()
                path.pop()
    return None


def topological_order(tasks: list[TemplateTask], edges: list[TemplateEdge]) -> list[TemplateTask]:
    """Tasks in dependency order. Ties break on (ordinal, task_ref) so the result is stable —
    an unstable order would make a launched project's item numbering vary run to run."""
    by_ref = {t.task_ref: t for t in tasks}
    indegree = dict.fromkeys(by_ref, 0)
    successors: dict[str, list[str]] = {ref: [] for ref in by_ref}
    for edge in edges:
        indegree[edge.task_ref] += 1
        successors[edge.depends_on_ref].append(edge.task_ref)

    ready = sorted(
        (ref for ref, deg in indegree.items() if deg == 0),
        key=lambda r: (by_ref[r].ordinal, r),
    )
    ordered: list[TemplateTask] = []
    while ready:
        ref = ready.pop(0)
        ordered.append(by_ref[ref])
        for successor in successors[ref]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
        ready.sort(key=lambda r: (by_ref[r].ordinal, r))

    if len(ordered) != len(by_ref):
        raise TemplateInvalid(["dependency cycle prevents ordering"])
    return ordered


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    task: TemplateTask
    start_date: date
    due_date: date


@dataclass
class Schedule:
    tasks: list[ScheduledTask] = field(default_factory=list)
    target_end: date | None = None

    def by_ref(self, ref: str) -> ScheduledTask:
        return next(s for s in self.tasks if s.task.task_ref == ref)


def compute_schedule(
    tasks: list[TemplateTask],
    edges: list[TemplateEdge],
    start: date,
    holidays: dict[date, str],
) -> Schedule:
    """Forward-pass schedule for a launched project.

    Each task starts on the later of (a) the project start plus its own offset and (b) the day
    after its last predecessor finishes — the standard earliest-start computation. Everything is
    in business days, so a task never silently absorbs a weekend.

    A zero-cycle task (a milestone) starts and finishes the same day: it marks a moment, it does
    not consume working time.
    """
    ordered = topological_order(tasks, edges)
    predecessors: dict[str, list[str]] = {t.task_ref: [] for t in tasks}
    for edge in edges:
        predecessors[edge.task_ref].append(edge.depends_on_ref)

    scheduled: dict[str, ScheduledTask] = {}
    for task in ordered:
        earliest = add_business_days(start, task.start_offset_days, holidays)
        for predecessor in predecessors[task.task_ref]:
            finish = scheduled[predecessor].due_date
            after = add_business_days(finish, 1, holidays)
            earliest = max(earliest, after)
        due = (
            add_business_days(earliest, task.cycle_days, holidays)
            if task.cycle_days else earliest
        )
        scheduled[task.task_ref] = ScheduledTask(task=task, start_date=earliest, due_date=due)

    result = [scheduled[t.task_ref] for t in ordered]
    return Schedule(tasks=result, target_end=max((s.due_date for s in result), default=None))


# ---------------------------------------------------------------------------
# Persistence + launch
# ---------------------------------------------------------------------------


def load_template(conn: psycopg.Connection, template_id: UUID) -> tuple[
    list[TemplateTask], list[TemplateEdge]
]:
    task_rows = conn.execute(
        """
        select t.task_ref, t.title, t.role, t.cycle_days, t.start_offset_days,
               s.name, t.is_milestone, t.ordinal
          from app.project_template_tasks t
          left join app.project_template_stages s on s.id = t.stage_id
         where t.template_id = %s
         order by t.ordinal, t.task_ref
        """,
        (template_id,),
    ).fetchall()
    edge_rows = conn.execute(
        "select task_ref, depends_on_ref from app.project_template_dependencies "
        " where template_id = %s",
        (template_id,),
    ).fetchall()
    tasks = [
        TemplateTask(task_ref=r[0], title=r[1], role=r[2], cycle_days=r[3],
                     start_offset_days=r[4], stage=r[5], is_milestone=r[6], ordinal=r[7])
        for r in task_rows
    ]
    edges = [TemplateEdge(task_ref=r[0], depends_on_ref=r[1]) for r in edge_rows]
    return tasks, edges


def publish_template(conn: psycopg.Connection, template_id: UUID) -> None:
    """Validate and publish, retiring whatever version currently holds the key.

    Publishing is the gate because it is the moment a template starts producing real work. A
    draft may be as broken as its author needs while being written.
    """
    tasks, edges = load_template(conn, template_id)
    validate_template(tasks, edges)

    row = conn.execute(
        "select key from app.project_templates where id = %s", (template_id,)
    ).fetchone()
    if row is None:
        raise LookupError("template not found or not visible")
    conn.execute(
        "update app.project_templates set status = 'retired' "
        " where key = %s and status = 'published' and id <> %s",
        (row[0], template_id),
    )
    conn.execute(
        "update app.project_templates set status = 'published', published_at = now() "
        " where id = %s",
        (template_id,),
    )


def new_version(conn: psycopg.Connection, template_id: UUID, created_by: UUID) -> UUID:
    """Clone a template into a fresh draft version.

    This is how a published template is edited: never in place. In-flight projects keep citing
    the version they launched from, so "why does this project have a review step?" stays
    answerable after the step is removed from v4.
    """
    row = conn.execute(
        "select key, name, description, jurisdiction, matter_type from app.project_templates "
        " where id = %s",
        (template_id,),
    ).fetchone()
    if row is None:
        raise LookupError("template not found or not visible")
    key = row[0]
    next_version = conn.execute(
        "select coalesce(max(version), 0) + 1 from app.project_templates where key = %s", (key,)
    ).fetchone()
    assert next_version is not None

    created = conn.execute(
        "insert into app.project_templates "
        "  (key, version, name, description, jurisdiction, matter_type, status, created_by) "
        "values (%s, %s, %s, %s, %s, %s, 'draft', %s) returning id",
        (key, next_version[0], row[1], row[2], row[3], row[4], created_by),
    ).fetchone()
    assert created is not None
    new_id = UUID(str(created[0]))

    # Stages first: tasks reference them.
    stage_map: dict[str, UUID] = {}
    for stage in conn.execute(
        "select id, ordinal, name from app.project_template_stages where template_id = %s",
        (template_id,),
    ).fetchall():
        inserted = conn.execute(
            "insert into app.project_template_stages (template_id, ordinal, name) "
            "values (%s, %s, %s) returning id",
            (new_id, stage[1], stage[2]),
        ).fetchone()
        assert inserted is not None
        stage_map[str(stage[0])] = UUID(str(inserted[0]))

    for task in conn.execute(
        "select stage_id, task_ref, title, description, role, cycle_days, start_offset_days, "
        "       is_milestone, ordinal "
        "  from app.project_template_tasks where template_id = %s",
        (template_id,),
    ).fetchall():
        conn.execute(
            "insert into app.project_template_tasks "
            "  (template_id, stage_id, task_ref, title, description, role, cycle_days, "
            "   start_offset_days, is_milestone, ordinal) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (new_id, stage_map.get(str(task[0])) if task[0] else None, task[1], task[2],
             task[3], task[4], task[5], task[6], task[7], task[8]),
        )

    conn.execute(
        "insert into app.project_template_dependencies (template_id, task_ref, depends_on_ref) "
        "select %s, task_ref, depends_on_ref from app.project_template_dependencies "
        " where template_id = %s",
        (new_id, template_id),
    )
    return new_id


def resolve_roles(conn: psycopg.Connection, roles: list[str]) -> dict[str, UUID | None]:
    """Role → default assignee. WP 5.2 replaces this with workload-aware assignment; the seam is
    here so that lands as one function swap rather than a change at every launch site."""
    if not roles:
        return {}
    rows = conn.execute(
        "select role, user_id from app.role_assignments where role = any(%s)", (roles,)
    ).fetchall()
    mapping: dict[str, UUID | None] = {role: None for role in roles}
    for role, user_id in rows:
        mapping[role] = UUID(str(user_id)) if user_id else None
    return mapping


def launch_project(
    conn: psycopg.Connection,
    template_id: UUID,
    name: str,
    created_by: UUID,
    start: date,
    holidays: dict[date, str] | None = None,
    matter_id: UUID | None = None,
    family_id: UUID | None = None,
) -> UUID:
    """Instantiate a template into a project of scheduled, chained, routed work items.

    Only a published template may be launched: a draft is by definition unvalidated, and a
    project built from one can contain a dependency cycle whose tasks never all complete.
    """
    meta = conn.execute(
        "select key, version, status from app.project_templates where id = %s", (template_id,)
    ).fetchone()
    if meta is None:
        raise LookupError("template not found or not visible")
    key, version, status = meta
    if status != "published":
        raise ValueError(f"template {key!r} v{version} is {status}, not published")

    tasks, edges = load_template(conn, template_id)
    validate_template(tasks, edges)
    schedule = compute_schedule(tasks, edges, start, holidays or {})
    assignees = resolve_roles(conn, sorted({t.role for t in tasks if t.role}))

    row = conn.execute(
        """
        insert into app.projects
          (name, matter_id, family_id, template_id, template_key, template_version,
           started_on, target_end, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id
        """,
        (name, matter_id, family_id, template_id, key, version, start, schedule.target_end,
         created_by),
    ).fetchone()
    assert row is not None
    project_id = UUID(str(row[0]))

    item_ids: dict[str, UUID] = {}
    for ordinal, scheduled in enumerate(schedule.tasks):
        task = scheduled.task
        inserted = conn.execute(
            """
            insert into app.work_items
              (title, matter_id, assignee_id, status, due_date, created_by,
               project_id, stage_name, task_ref, role, started_on, ordinal)
            values (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (task.title, matter_id, assignees.get(task.role) if task.role else None,
             scheduled.due_date, created_by, project_id, task.stage, task.task_ref, task.role,
             scheduled.start_date, ordinal),
        ).fetchone()
        assert inserted is not None
        item_ids[task.task_ref] = UUID(str(inserted[0]))

    for edge in edges:
        conn.execute(
            "insert into app.work_item_dependencies (work_item_id, depends_on_id) "
            "values (%s, %s) on conflict do nothing",
            (item_ids[edge.task_ref], item_ids[edge.depends_on_ref]),
        )
    return project_id


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------


def complete_work_item(conn: psycopg.Connection, item_id: UUID) -> list[UUID]:
    """Mark an item done and return the successors it just unblocked.

    Returning the newly-unblocked items is what makes chaining visible: the caller notifies their
    assignees. Nothing is "spawned" — the whole graph was instantiated at launch, so completion
    only changes what is *reachable*, never what exists. That keeps the project plan stable and
    reviewable from the moment it starts.
    """
    blocked = conn.execute(
        "select 1 from app.work_item_dependencies d join app.work_items w on w.id = d.depends_on_id"
        " where d.work_item_id = %s and w.status not in ('done', 'cancelled')",
        (item_id,),
    ).fetchone()
    if blocked is not None:
        raise ValueError("cannot complete a blocked work item")

    updated = conn.execute(
        "update app.work_items set status = 'done', completed_on = current_date "
        " where id = %s and status <> 'done' returning id",
        (item_id,),
    ).fetchone()
    if updated is None:
        raise LookupError("work item not found, not visible, or already done")

    successors = conn.execute(
        "select work_item_id from app.work_item_dependencies where depends_on_id = %s", (item_id,)
    ).fetchall()
    unblocked: list[UUID] = []
    for (successor_id,) in successors:
        still = conn.execute(
            "select app.work_item_is_blocked(%s)", (successor_id,)
        ).fetchone()
        if still is not None and not still[0]:
            unblocked.append(UUID(str(successor_id)))
    return unblocked


def project_progress(conn: psycopg.Connection, project_id: UUID) -> dict[str, int]:
    """Counts by status — the number a board header shows."""
    rows = conn.execute(
        "select status, count(*) from app.work_items where project_id = %s group by status",
        (project_id,),
    ).fetchall()
    return {status: count for status, count in rows}
