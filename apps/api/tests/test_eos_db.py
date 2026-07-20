"""EOS core against Postgres (WP 5.7, §M9) — scorecard view, IDS lifecycle, To-Do tracking."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import eos

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.issues')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.7 migration (0023) not applied")


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


# --- scorecard -----------------------------------------------------------------


def test_scorecard_current_colours_the_latest_value(su: psycopg.Connection) -> None:
    mid = su.execute(
        "insert into app.scorecard_measurables (name, owner_id, goal, direction) "
        "values ('On-time %%', %s, 90, 'higher_is_better') returning id",
        (ADMIN_ID,),
    ).fetchone()[0]
    su.execute(
        "insert into app.scorecard_entries (measurable_id, week_start, value) "
        "values (%s, '2026-07-13', 70), (%s, '2026-07-20', 95)",
        (mid, mid),
    )
    try:
        rag = su.execute(
            "select value, rag from app.scorecard_current where id = %s", (mid,)
        ).fetchone()
        assert rag[0] == 95           # the latest week's value, not the older one
        assert rag[1] == "green"
    finally:
        su.execute("delete from app.scorecard_measurables where id = %s", (mid,))


def test_sql_rag_matches_the_python_helper(su: psycopg.Connection) -> None:
    """The SQL and Python RAG must never disagree about a colour."""
    for value, goal, direction, band in [
        (95, 100, "higher_is_better", 0.1),
        (80, 100, "higher_is_better", 0.1),
        (6, 5, "lower_is_better", 0.1),
    ]:
        sql = su.execute(
            "select app.scorecard_rag(%s::numeric, %s::numeric, %s::app.goal_direction, "
            "%s::numeric)",
            (value, goal, direction, band),
        ).fetchone()[0]
        assert sql == eos.rag_status(value, goal, direction, band)


# --- IDS lifecycle -------------------------------------------------------------


def _issue(su: psycopg.Connection, **kw: object) -> str:
    fields = {"title": "Something is off", "raised_by": ADMIN_ID}
    fields.update(kw)
    cols = ", ".join(fields)
    ph = ", ".join(["%s"] * len(fields))
    return str(su.execute(
        f"insert into app.issues ({cols}) values ({ph}) returning id", tuple(fields.values())
    ).fetchone()[0])


def test_an_issue_advances_through_ids(su: psycopg.Connection) -> None:
    iid = _issue(su)
    assert eos.advance_issue(su, uuid.UUID(iid), "identified") == "identified"
    assert eos.advance_issue(su, uuid.UUID(iid), "discussing") == "discussing"
    assert eos.advance_issue(su, uuid.UUID(iid), "solved", resolution="did the thing") == "solved"


def test_solving_requires_a_resolution(su: psycopg.Connection) -> None:
    """An issue closed with no recorded decision teaches the team the IDS list is where problems
    go to be forgotten."""
    iid = _issue(su)
    with pytest.raises(eos.IssueTransitionError, match="resolution"):
        eos.advance_issue(su, uuid.UUID(iid), "solved")


def test_an_issue_cannot_move_backward(su: psycopg.Connection) -> None:
    iid = _issue(su)
    eos.advance_issue(su, uuid.UUID(iid), "discussing")
    with pytest.raises(eos.IssueTransitionError, match="back"):
        eos.advance_issue(su, uuid.UUID(iid), "identified")


def test_a_solved_issue_cannot_be_reopened(su: psycopg.Connection) -> None:
    iid = _issue(su)
    eos.advance_issue(su, uuid.UUID(iid), "solved", resolution="done")
    with pytest.raises(eos.IssueTransitionError, match="cannot be reopened"):
        eos.advance_issue(su, uuid.UUID(iid), "discussing")


def test_an_issue_can_be_dropped_from_an_open_state(su: psycopg.Connection) -> None:
    iid = _issue(su)
    eos.advance_issue(su, uuid.UUID(iid), "identified")
    assert eos.advance_issue(su, uuid.UUID(iid), "dropped") == "dropped"


def test_solving_with_a_todo_spawns_a_linked_action(su: psycopg.Connection) -> None:
    """The common L10 move: solving an issue means agreeing who does what next."""
    iid = _issue(su)
    todo_id = eos.solve_issue_with_todo(
        su, uuid.UUID(iid), "Rebuild the intake checklist", "Draft new checklist",
        uuid.UUID(STAFF_ID),
    )
    link = su.execute(
        "select from_issue_id from app.eos_todos where id = %s", (todo_id,)
    ).fetchone()[0]
    assert str(link) == iid
    status = su.execute("select status::text from app.issues where id = %s", (iid,)).fetchone()[0]
    assert status == "solved"


# --- To-Do tracking ------------------------------------------------------------


def test_todo_score_counts_committed_and_done(su: psycopg.Connection) -> None:
    owner = su.execute(
        "insert into app.os_users (id, email, display_name, is_active) "
        "values (gen_random_uuid(), %s, 'ToDo Owner', true) returning id",
        (f"todo-{uuid.uuid4().hex[:6]}@brunetco.com",),
    ).fetchone()[0]
    try:
        ids = []
        for i in range(4):
            ids.append(su.execute(
                "insert into app.eos_todos (title, owner_id) values (%s, %s) returning id",
                (f"todo {i}", owner),
            ).fetchone()[0])
        eos.complete_todo(su, uuid.UUID(str(ids[0])))
        eos.complete_todo(su, uuid.UUID(str(ids[1])))
        score = eos.todo_score(su, uuid.UUID(str(owner)))
        assert score.committed == 4 and score.done == 2
        assert score.rate == 0.5
    finally:
        su.execute("delete from app.eos_todos where owner_id = %s", (owner,))
        su.execute("delete from app.os_users where id = %s", (owner,))


def test_completing_a_todo_is_idempotent(su: psycopg.Connection) -> None:
    tid = su.execute(
        "insert into app.eos_todos (title, owner_id) values ('t', %s) returning id", (ADMIN_ID,)
    ).fetchone()[0]
    try:
        eos.complete_todo(su, uuid.UUID(str(tid)))
        first = su.execute("select done_at from app.eos_todos where id = %s", (tid,)).fetchone()[0]
        eos.complete_todo(su, uuid.UUID(str(tid)))   # no-op
        second = su.execute("select done_at from app.eos_todos where id = %s", (tid,)).fetchone()[0]
        assert first == second
    finally:
        su.execute("delete from app.eos_todos where id = %s", (tid,))


# --- rocks + RLS ---------------------------------------------------------------


def test_rock_summary_counts_by_status(su: psycopg.Connection) -> None:
    q = f"2026-Q{uuid.uuid4().int % 9 + 1}-{uuid.uuid4().hex[:4]}"
    su.execute(
        "insert into app.rocks (title, owner_id, quarter, status) values "
        "('R1', %s, %s, 'on_track'), ('R2', %s, %s, 'off_track'), ('R3', %s, %s, 'done')",
        (ADMIN_ID, q, ADMIN_ID, q, ADMIN_ID, q),
    )
    try:
        summary = eos.quarter_rock_summary(su, q)
        assert summary == {"on_track": 1, "off_track": 1, "done": 1}
    finally:
        su.execute("delete from app.rocks where quarter = %s", (q,))


def test_eos_data_is_staff_visible(su: psycopg.Connection) -> None:
    """EOS is run in the open — the team reviews the scorecard together in the L10."""
    iid = _issue(su)
    try:
        with _user_conn(STAFF_ID) as conn:
            n = conn.execute(
                "select count(*) from app.issues where id = %s", (iid,)
            ).fetchone()[0]
            assert n == 1
    finally:
        su.execute("delete from app.issues where id = %s", (iid,))
