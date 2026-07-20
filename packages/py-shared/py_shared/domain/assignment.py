"""Assignment engine + capacity + SLA escalation (WP 5.2, spec §M9, D30).

Replaces the Word project lists. Three jobs:

  * **Suggest who should do a task** — from the pool of people who *can* do the role (their
    capability), weighted by live workload first and historical cycle time on that task type
    second. Workload dominates: the spec frames this as "live workload and historical cycle time",
    in that order, and a firm balances load before it optimises for the fastest hands.

  * **Reassign** — drag-to-reassign is a real move of someone's work, logged so "why is this mine
    now?" is answerable.

  * **Escalate SLA breaches** — an overdue task that only turns red on a board is a task whose
    escalation nobody owns. The sweep routes each breach to the matter's responsible professional.

The scoring is a pure, testable sort key rather than an opaque weighted float: a firm owner
overruling a suggestion should be able to see *why* it was made, and a lexicographic key
(capacity, then load, then speed) is legible in a way that `0.3·a + 0.7·b` is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

import psycopg

# When a candidate has no explicit max_concurrent, this is the load at which the scorer starts
# treating them as full for the capacity comparison. Not a hard cap — assignment never refuses,
# it only prefers the least-loaded eligible person.
DEFAULT_SOFT_CAP = 12

# Cycle-time stand-in for a candidate with no history on this task type: the task-type baseline
# if one exists, else this neutral value — so a newcomer ranks with the average, neither rewarded
# nor punished for the absence of a track record.
_NEUTRAL_CYCLE_DAYS = 999.0


@dataclass(frozen=True)
class Candidate:
    user_id: UUID
    display_name: str
    open_load: int
    max_concurrent: int | None = None
    avg_cycle_days: float | None = None
    baseline_cycle_days: float | None = None

    @property
    def at_capacity(self) -> bool:
        return self.max_concurrent is not None and self.open_load >= self.max_concurrent

    @property
    def _cycle_key(self) -> float:
        if self.avg_cycle_days is not None:
            return self.avg_cycle_days
        if self.baseline_cycle_days is not None:
            return self.baseline_cycle_days
        return _NEUTRAL_CYCLE_DAYS


def candidate_sort_key(c: Candidate) -> tuple[int, int, float, str]:
    """Best candidate sorts first. Lexicographic and deliberately legible:

      1. not-at-capacity before at-capacity — never pile onto someone already full while an
         under-cap colleague is free;
      2. lower open load — balance the work;
      3. faster historical cycle time on this task type — the tie-breaker, not the driver;
      4. display name — a stable final tie-break so the order never wobbles run to run.
    """
    return (int(c.at_capacity), c.open_load, c._cycle_key, c.display_name)


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=candidate_sort_key)


# ---------------------------------------------------------------------------
# DB-backed suggestion
# ---------------------------------------------------------------------------


def _candidates_for_role(
    conn: psycopg.Connection, role: str, task_ref: str | None,
) -> list[Candidate]:
    """The capability pool for ``role``, hydrated with live load and cycle-time history.

    Empty when nobody is registered for the role — the caller then falls back to the single
    default assignment (0018), or to leaving the item unassigned.
    """
    baseline: float | None = None
    if task_ref is not None:
        row = conn.execute(
            "select avg(avg_cycle_days)::float from app.user_task_cycle_stats where task_ref = %s",
            (task_ref,),
        ).fetchone()
        baseline = row[0] if row else None

    rows = conn.execute(
        """
        select ur.user_id, u.display_name, ur.max_concurrent,
               coalesce(wl.open_load, 0) as open_load,
               cs.avg_cycle_days::float
          from app.user_roles ur
          join app.os_users u on u.id = ur.user_id and u.is_active
          left join app.user_workload wl on wl.user_id = ur.user_id
          left join app.user_task_cycle_stats cs
                 on cs.user_id = ur.user_id and cs.task_ref = %s
         where ur.role = %s
        """,
        (task_ref, role),
    ).fetchall()

    return [
        Candidate(
            user_id=UUID(str(r[0])), display_name=r[1], max_concurrent=r[2],
            open_load=r[3], avg_cycle_days=r[4], baseline_cycle_days=baseline,
        )
        for r in rows
    ]


def suggest_assignees(
    conn: psycopg.Connection, role: str, task_ref: str | None = None, limit: int = 5,
) -> list[Candidate]:
    """Ranked assignment suggestions for a role (optionally for a specific task type).

    Returns candidates best-first. An empty list means nobody holds the capability — a real
    answer the caller must handle, not an error.
    """
    return rank_candidates(_candidates_for_role(conn, role, task_ref))[:limit]


def best_assignee(
    conn: psycopg.Connection, role: str, task_ref: str | None = None,
) -> UUID | None:
    """The single best assignee for a role, with the 0018 fallback chain:

    capability-pool best  →  role_assignments default  →  unassigned.

    This is what `projects.resolve_roles` now delegates to, so launching a project routes work by
    live load instead of to one fixed person — while a role with only a default still resolves to
    that default, and an unstaffed role still leaves the item unassigned.
    """
    ranked = suggest_assignees(conn, role, task_ref, limit=1)
    if ranked:
        return ranked[0].user_id
    default = conn.execute(
        "select user_id from app.role_assignments where role = %s", (role,)
    ).fetchone()
    return UUID(str(default[0])) if default and default[0] else None


def resolve_roles(conn: psycopg.Connection, roles: list[str]) -> dict[str, UUID | None]:
    """Role → best current assignee, for project launch (WP 5.1's seam). Workload-aware."""
    return {role: best_assignee(conn, role, None) for role in roles}


# ---------------------------------------------------------------------------
# Reassignment
# ---------------------------------------------------------------------------


def reassign_work_item(
    conn: psycopg.Connection,
    item_id: UUID,
    to_user: UUID | None,
    reassigned_by: UUID,
    reason: str | None = None,
) -> None:
    """Move a work item to another person (or park it, ``to_user=None``) and log the move.

    The log is the point: reassignment silently changes whose queue a task sits in, and a capacity
    board people trust needs to answer "who moved this, and why?". A no-op move (same assignee) is
    still recorded, because "confirmed it stays with X" is itself a decision worth a trace.
    """
    row = conn.execute(
        "select assignee_id from app.work_items where id = %s", (item_id,)
    ).fetchone()
    if row is None:
        raise LookupError("work item not found or not visible")
    from_user = row[0]

    conn.execute(
        "update app.work_items set assignee_id = %s where id = %s", (to_user, item_id)
    )
    conn.execute(
        """
        insert into app.work_item_reassignments
          (work_item_id, from_user_id, to_user_id, reason, reassigned_by)
        values (%s, %s, %s, %s, %s)
        """,
        (item_id, from_user, to_user, reason, reassigned_by),
    )


# ---------------------------------------------------------------------------
# SLA escalation
# ---------------------------------------------------------------------------


@dataclass
class Escalation:
    work_item_id: UUID
    escalated_to: UUID | None
    reason: str
    due_date: date | None


def sweep_sla_breaches(conn: psycopg.Connection, today: date | None = None) -> list[Escalation]:
    """Open one escalation per newly-breached work item; return the escalations opened.

    A breach is an open/in-progress item past its due date. The escalation is routed to the
    matter's responsible professional (`matters.responsible_user_id`); a firm-general item with no
    matter escalates to nobody in particular but is still recorded, so it surfaces on the
    escalations list rather than vanishing.

    Idempotent: the partial unique index (one open escalation per item) plus the `on conflict`
    guard mean re-running the sweep never stacks duplicates — the same breach is raised once and
    stays raised until a human resolves it.
    """
    reference = today or date.today()
    rows = conn.execute(
        """
        select w.id, w.due_date, w.assignee_id, m.responsible_user_id
          from app.work_items w
          left join app.matters m on m.id = w.matter_id
         where w.status in ('open', 'in_progress')
           and w.due_date is not null
           and w.due_date < %s
           and not exists (
             select 1 from app.escalations e
              where e.work_item_id = w.id and e.resolved_at is null
           )
        """,
        (reference,),
    ).fetchall()

    opened: list[Escalation] = []
    for item_id, due_date, assignee_id, responsible_id in rows:
        reason = "unassigned_overdue" if assignee_id is None else "sla_breach"
        inserted = conn.execute(
            """
            insert into app.escalations (work_item_id, escalated_to, reason, due_date)
            values (%s, %s, %s, %s)
            on conflict (work_item_id) where resolved_at is null do nothing
            returning id
            """,
            (item_id, responsible_id, reason, due_date),
        ).fetchone()
        if inserted is not None:
            opened.append(Escalation(
                work_item_id=UUID(str(item_id)),
                escalated_to=UUID(str(responsible_id)) if responsible_id else None,
                reason=reason, due_date=due_date,
            ))
    return opened


def resolve_escalation(conn: psycopg.Connection, escalation_id: UUID) -> None:
    """Close an escalation. Only an open one can be resolved (idempotent-safe)."""
    updated = conn.execute(
        "update app.escalations set resolved_at = now() "
        " where id = %s and resolved_at is null returning id",
        (escalation_id,),
    ).fetchone()
    if updated is None:
        raise LookupError("escalation not found, not visible, or already resolved")
