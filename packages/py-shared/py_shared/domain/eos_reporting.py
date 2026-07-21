"""L10 pack, 1-on-1 reports, and scorecard auto-population (WP 5.8, spec §M9).

WP 5.7 built the EOS containers; this fills them from live OS data and assembles the two
recurring documents the firm actually runs on:

  * **The L10 pack** — firm scorecard with RAG, Rocks status, To-Do completion, overdue/at-risk by
    owner, aging WIP, next-7-day critical deadlines. Generated, not typed up, because a pack
    someone has to assemble by hand is a pack that stops being assembled by week six.

  * **Per-person 1-on-1 reports** — the same metrics scoped to one seat plus their open items.
    Spec §M9 is explicit that these are *seat-owned measurables, not surveillance*: the report is
    built for the person whose seat it is, and it shows them what they own.

Auto-population is the other half. A measurable carrying a `source_metric` is computed from live
data each week rather than typed in — which is what stops the scorecard drifting into a
hand-maintained spreadsheet that nobody trusts. A measurable with no source_metric stays manual by
design (some numbers genuinely come from outside the system).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg

from py_shared.domain import eos

# Work older than this and still open counts as aging WIP — the number an L10 asks about.
AGING_WIP_DAYS = 14
# "Critical deadlines" horizon for the pack.
DEADLINE_HORIZON_DAYS = 7


# ---------------------------------------------------------------------------
# Auto-populated measurables
# ---------------------------------------------------------------------------

# source_metric key -> what it measures. Each is computed per owner, per week.
ON_TIME_COMPLETION_PCT = "on_time_completion_pct"
OVERDUE_OPEN_COUNT = "overdue_open_count"
TODO_COMPLETION_PCT = "todo_completion_pct"
MICRO_REQUEST_TURNAROUND_HOURS = "micro_request_turnaround_hours"
AGING_WIP_COUNT = "aging_wip_count"

SUPPORTED_METRICS = frozenset({
    ON_TIME_COMPLETION_PCT,
    OVERDUE_OPEN_COUNT,
    TODO_COMPLETION_PCT,
    MICRO_REQUEST_TURNAROUND_HOURS,
    AGING_WIP_COUNT,
})


class UnknownMetric(ValueError):
    """A measurable names a source_metric this module cannot compute.

    Raised rather than silently skipped: a scorecard row that quietly stops updating still shows
    its last value, which reads as "the number held steady" when it actually means "nobody is
    measuring this any more". Loud beats stale.
    """


def compute_metric(
    conn: psycopg.Connection, metric: str, owner_id: UUID, as_of: date,
) -> float | None:
    """Compute one measurable's value for an owner from live OS data.

    Returns None where there is genuinely nothing to measure (no completed work, no requests) —
    distinct from 0.0, which is a real measurement. The scorecard renders None as 'no_data' rather
    than a red zero, because "nothing happened" and "everything failed" are different weeks.
    """
    if metric == ON_TIME_COMPLETION_PCT:
        row = conn.execute(
            """
            select count(*),
                   count(*) filter (where completed_on <= due_date)
              from app.work_items
             where assignee_id = %s and status = 'done'
               and due_date is not null and completed_on is not null
            """,
            (owner_id,),
        ).fetchone()
        assert row is not None
        total, on_time = row
        return round(on_time * 100.0 / total, 2) if total else None

    if metric == OVERDUE_OPEN_COUNT:
        row = conn.execute(
            "select count(*) from app.work_items "
            " where assignee_id = %s and status in ('open', 'in_progress') "
            "   and due_date is not null and due_date < %s",
            (owner_id, as_of),
        ).fetchone()
        assert row is not None
        return float(row[0])

    if metric == TODO_COMPLETION_PCT:
        score = eos.todo_score(conn, owner_id)
        return round(score.rate * 100.0, 2) if score.committed else None

    if metric == MICRO_REQUEST_TURNAROUND_HOURS:
        row = conn.execute(
            "select avg(turnaround_hours) from app.micro_request_turnaround "
            " where assignee_id = %s and resolved_at is not null",
            (owner_id,),
        ).fetchone()
        assert row is not None
        return round(float(row[0]), 2) if row[0] is not None else None

    if metric == AGING_WIP_COUNT:
        cutoff = as_of - timedelta(days=AGING_WIP_DAYS)
        row = conn.execute(
            "select count(*) from app.work_items "
            " where assignee_id = %s and status in ('open', 'in_progress') and created_at < %s",
            (owner_id, cutoff),
        ).fetchone()
        assert row is not None
        return float(row[0])

    raise UnknownMetric(f"no computation registered for source_metric {metric!r}")


def week_start_for(day: date) -> date:
    """The Monday of the scorecard week containing ``day``. Scorecard weeks are Monday-anchored so
    a Friday entry and the following Monday's review refer to the same week."""
    return day - timedelta(days=day.weekday())


def populate_scorecard(
    conn: psycopg.Connection, as_of: date | None = None,
) -> dict[str, int]:
    """Compute and upsert every auto-populated measurable for the current week.

    Idempotent: re-running the same week overwrites with a freshly-computed value rather than
    appending, so this can run nightly and on demand before an L10 without double-counting.
    Manual measurables (no source_metric) are left untouched — the job never overwrites a number a
    human typed.
    """
    today = as_of or date.today()
    week = week_start_for(today)
    rows = conn.execute(
        "select id, owner_id, source_metric from app.scorecard_measurables "
        " where is_active and source_metric is not null"
    ).fetchall()

    written = 0
    skipped = 0
    for measurable_id, owner_id, metric in rows:
        value = compute_metric(conn, metric, UUID(str(owner_id)), today)
        if value is None:
            skipped += 1
            continue
        conn.execute(
            """
            insert into app.scorecard_entries (measurable_id, week_start, value)
            values (%s, %s, %s)
            on conflict (measurable_id, week_start)
            do update set value = excluded.value, entered_at = now()
            """,
            (measurable_id, week, value),
        )
        written += 1
    return {"written": written, "no_data": skipped}


# ---------------------------------------------------------------------------
# The L10 pack
# ---------------------------------------------------------------------------


@dataclass
class L10Pack:
    week_start: date
    quarter: str
    scorecard: list[dict[str, Any]] = field(default_factory=list)
    rocks: dict[str, int] = field(default_factory=dict)
    todo_completion: dict[str, Any] = field(default_factory=dict)
    overdue_by_owner: list[dict[str, Any]] = field(default_factory=list)
    aging_wip: list[dict[str, Any]] = field(default_factory=list)
    critical_deadlines: list[dict[str, Any]] = field(default_factory=list)
    open_issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def headline(self) -> dict[str, Any]:
        """The four numbers that open the meeting."""
        reds = sum(1 for row in self.scorecard if row["rag"] == "red")
        return {
            "scorecard_red": reds,
            "rocks_off_track": self.rocks.get("off_track", 0),
            "todo_completion_pct": self.todo_completion.get("rate_pct", 0.0),
            "deadlines_next_7_days": len(self.critical_deadlines),
        }


def l10_pack(
    conn: psycopg.Connection, quarter: str, as_of: date | None = None,
) -> L10Pack:
    """Assemble the weekly L10 pack from live data."""
    today = as_of or date.today()
    pack = L10Pack(week_start=week_start_for(today), quarter=quarter)

    pack.scorecard = [
        {"id": r[0], "name": r[1], "owner_id": r[2], "goal": float(r[3]),
         "value": float(r[4]) if r[4] is not None else None, "rag": r[5]}
        for r in conn.execute(
            "select id, name, owner_id, goal, value, rag from app.scorecard_current order by name"
        ).fetchall()
    ]

    pack.rocks = eos.quarter_rock_summary(conn, quarter)

    todo_row = conn.execute(
        "select count(*), count(*) filter (where done) from app.eos_todos"
    ).fetchone()
    assert todo_row is not None
    committed, done = todo_row
    rate = (done / committed) if committed else 0.0
    pack.todo_completion = {
        "committed": committed,
        "done": done,
        "rate_pct": round(rate * 100.0, 1),
        # The firm-level read of the EOS 90% discipline.
        "meets_target": committed > 0 and rate >= eos.EOS_TODO_TARGET,
    }

    pack.overdue_by_owner = [
        {"user_id": r[0], "display_name": r[1], "overdue": r[2], "due_soon": r[3],
         "open_load": r[4]}
        for r in conn.execute(
            "select user_id, display_name, overdue, due_soon, open_load from app.user_workload "
            " where overdue > 0 or due_soon > 0 order by overdue desc, due_soon desc"
        ).fetchall()
    ]

    cutoff = today - timedelta(days=AGING_WIP_DAYS)
    pack.aging_wip = [
        {"id": r[0], "title": r[1], "assignee_id": r[2], "created_at": r[3]}
        for r in conn.execute(
            "select id, title, assignee_id, created_at from app.work_items "
            " where status in ('open', 'in_progress') and created_at < %s "
            " order by created_at limit 50",
            (cutoff,),
        ).fetchall()
    ]

    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    pack.critical_deadlines = [
        {"id": r[0], "title": r[1], "matter_id": r[2], "final_due_date": r[3],
         "assignee_id": r[4]}
        for r in conn.execute(
            "select id, title, matter_id, final_due_date, assignee_id from app.tasks "
            " where status = 'open' and final_due_date is not null "
            "   and final_due_date between %s and %s "
            " order by final_due_date",
            (today, horizon),
        ).fetchall()
    ]

    pack.open_issues = [
        {"id": r[0], "title": r[1], "status": r[2], "owner_id": r[3], "priority": r[4]}
        for r in conn.execute(
            "select id, title, status::text, owner_id, priority from app.issues "
            " where status not in ('solved', 'dropped') order by priority desc, created_at limit 50"
        ).fetchall()
    ]

    return pack


# ---------------------------------------------------------------------------
# 1-on-1 report
# ---------------------------------------------------------------------------


@dataclass
class OneOnOneReport:
    user_id: UUID
    week_start: date
    measurables: list[dict[str, Any]] = field(default_factory=list)
    rocks: list[dict[str, Any]] = field(default_factory=list)
    todo_score: dict[str, Any] = field(default_factory=dict)
    request_turnaround: dict[str, Any] = field(default_factory=dict)
    open_items: list[dict[str, Any]] = field(default_factory=list)


def one_on_one_report(
    conn: psycopg.Connection, user_id: UUID, quarter: str, as_of: date | None = None,
) -> OneOnOneReport:
    """The per-person report backing a supervision conversation.

    Scoped strictly to what this person owns — their measurables, their rocks, their to-dos, their
    request turnaround, their open work. Spec §M9 frames these as seat-owned measurables rather
    than surveillance, so the report contains nobody else's numbers to compare against.
    """
    today = as_of or date.today()
    report = OneOnOneReport(user_id=user_id, week_start=week_start_for(today))

    report.measurables = [
        {"name": r[0], "goal": float(r[1]), "value": float(r[2]) if r[2] is not None else None,
         "rag": r[3]}
        for r in conn.execute(
            "select name, goal, value, rag from app.scorecard_current "
            " where owner_id = %s order by name",
            (user_id,),
        ).fetchall()
    ]

    report.rocks = [
        {"id": r[0], "title": r[1], "status": r[2], "due_date": r[3]}
        for r in conn.execute(
            "select id, title, status::text, due_date from app.rocks "
            " where owner_id = %s and quarter = %s order by due_date nulls last",
            (user_id, quarter),
        ).fetchall()
    ]

    score = eos.todo_score(conn, user_id)
    report.todo_score = {
        "committed": score.committed, "done": score.done,
        "rate_pct": round(score.rate * 100.0, 1), "meets_target": score.meets_target,
    }

    from py_shared.domain import micro_requests as mr
    report.request_turnaround = dict(mr.turnaround_stats(conn, user_id))

    report.open_items = [
        {"id": r[0], "title": r[1], "due_date": r[2], "status": r[3], "is_blocked": r[4]}
        for r in conn.execute(
            "select id, title, due_date, status, is_blocked from app.work_item_queue "
            " where assignee_id = %s and status in ('open', 'in_progress') "
            " order by due_date nulls last limit 50",
            (user_id,),
        ).fetchall()
    ]

    return report
