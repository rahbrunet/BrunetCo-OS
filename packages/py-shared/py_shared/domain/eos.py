"""EOS core logic (WP 5.7, spec §M9, D30) — Scorecard RAG, IDS lifecycle, To-Do 90% tracking.

The tables are configuration; the behaviour worth testing is the computed and transitional logic:
how a measured value earns its colour, how an issue moves through Identify-Discuss-Solve, and how
a person's week measures against the EOS 90% To-Do target. All the pure functions here mirror the
SQL helper `app.scorecard_rag` so the API and the database never disagree about a colour.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

# EOS holds teams to completing 90% of the To-Dos they commit to each week.
EOS_TODO_TARGET = 0.90

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"


# ---------------------------------------------------------------------------
# Scorecard RAG (mirror of app.scorecard_rag)
# ---------------------------------------------------------------------------


def rag_status(
    value: float | None, goal: float, direction: str, yellow_band: float = 0.1,
) -> str:
    """Red / yellow / green (or no_data) for a measured value against its goal.

    Two goal directions because half a firm's measurables are lower-is-better (overdue count,
    days-to-file). The yellow band is the "close enough to be amber, not red" fraction — a value
    that misses goal but stays within the band is a warning, not a failure, and colouring it red
    would train people to ignore the scorecard's reds.
    """
    if value is None:
        return "no_data"
    if direction == HIGHER_IS_BETTER:
        if value >= goal:
            return "green"
        return "yellow" if value >= goal * (1 - yellow_band) else "red"
    # lower_is_better
    if value <= goal:
        return "green"
    return "yellow" if value <= goal * (1 + yellow_band) else "red"


# ---------------------------------------------------------------------------
# To-Do completion (the EOS 90% measurable)
# ---------------------------------------------------------------------------


@dataclass
class TodoScore:
    committed: int
    done: int

    @property
    def rate(self) -> float:
        return (self.done / self.committed) if self.committed else 0.0

    @property
    def meets_target(self) -> bool:
        # A week with nothing committed does not "pass" by vacuity — 0/0 is not 90%. Requiring at
        # least one commitment stops an idle week from flattering the number.
        return self.committed > 0 and self.rate >= EOS_TODO_TARGET


def todo_score(conn: psycopg.Connection, owner_id: UUID) -> TodoScore:
    """A person's To-Do completion, the seat-owned EOS measurable. Surfaces on their own view
    first (spec §M9: seat-owned, not surveillance)."""
    row = conn.execute(
        "select count(*), count(*) filter (where done) from app.eos_todos where owner_id = %s",
        (owner_id,),
    ).fetchone()
    assert row is not None
    return TodoScore(committed=row[0], done=row[1])


def complete_todo(conn: psycopg.Connection, todo_id: UUID) -> None:
    """Mark a To-Do done (idempotent — re-completing is a no-op, not an error)."""
    conn.execute(
        "update app.eos_todos set done = true, done_at = coalesce(done_at, now()) "
        " where id = %s and not done",
        (todo_id,),
    )


# ---------------------------------------------------------------------------
# Issues — the IDS lifecycle (Identify, Discuss, Solve)
# ---------------------------------------------------------------------------

# The legal forward progression. An issue advances through these; it may also be dropped from any
# non-solved state (a real EOS move — "this isn't actually an issue"), and solving requires a
# resolution so the list is a record of decisions, not just a graveyard of closed rows.
_IDS_ORDER = ["open", "identified", "discussing", "solved"]


class IssueTransitionError(ValueError):
    """An illegal issue-status move — reopening a solved issue, or solving with no resolution."""


def advance_issue(
    conn: psycopg.Connection,
    issue_id: UUID,
    to_status: str,
    resolution: str | None = None,
    now: datetime | None = None,
) -> str:
    """Move an issue through IDS. Enforces the rules that keep the list meaningful:

      * you cannot move backward (an issue that was discussed does not un-discuss);
      * solving REQUIRES a resolution — an issue closed with no recorded decision teaches the team
        that the IDS list is where problems go to be forgotten;
      * 'dropped' is reachable from any open state (legitimately deciding it is not an issue), but
        a solved issue is closed for good.
    """
    row = conn.execute(
        "select status::text from app.issues where id = %s", (issue_id,)
    ).fetchone()
    if row is None:
        raise LookupError("issue not found or not visible")
    current = row[0]

    if current in ("solved", "dropped"):
        raise IssueTransitionError(f"issue is {current}; it cannot be reopened")

    if to_status == "dropped":
        conn.execute(
            "update app.issues set status = 'dropped' where id = %s", (issue_id,)
        )
        return "dropped"

    if to_status == "solved":
        if not (resolution and resolution.strip()):
            raise IssueTransitionError("solving an issue requires a resolution")
        stamp = now or datetime.now().astimezone()
        conn.execute(
            "update app.issues set status = 'solved', resolution = %s, solved_at = %s "
            " where id = %s",
            (resolution, stamp, issue_id),
        )
        return "solved"

    if to_status not in _IDS_ORDER:
        raise IssueTransitionError(f"unknown issue status {to_status!r}")
    if _IDS_ORDER.index(to_status) < _IDS_ORDER.index(current):
        raise IssueTransitionError(f"cannot move an issue from {current!r} back to {to_status!r}")
    conn.execute(
        "update app.issues set status = %s::app.issue_status where id = %s",
        (to_status, issue_id),
    )
    return to_status


def solve_issue_with_todo(
    conn: psycopg.Connection, issue_id: UUID, resolution: str, todo_title: str, owner_id: UUID,
) -> UUID:
    """Solve an issue and spawn the To-Do that carries out the decision — the common L10 move,
    where "solving" an issue means agreeing who does what next. The To-Do links back to the issue
    so the accountability chain is visible."""
    advance_issue(conn, issue_id, "solved", resolution=resolution)
    row = conn.execute(
        "insert into app.eos_todos (title, owner_id, from_issue_id) values (%s, %s, %s) "
        "returning id",
        (todo_title, owner_id, issue_id),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


# ---------------------------------------------------------------------------
# Rocks
# ---------------------------------------------------------------------------


def quarter_rock_summary(conn: psycopg.Connection, quarter: str) -> dict[str, int]:
    """Counts by status for a quarter's rocks — the roll-up an L10 opens with."""
    rows = conn.execute(
        "select status::text, count(*) from app.rocks where quarter = %s group by status",
        (quarter,),
    ).fetchall()
    return {status: count for status, count in rows}
