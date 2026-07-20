"""Assignment engine, capacity board and SLA escalation against Postgres (WP 5.2, §M9).

Exercises what only exists in the database: the capability pool, live-workload and cycle-time
views, the launch-time fallback chain, reassignment audit, the idempotent escalation sweep, and
the admin gate on the capability pool.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import assignment as asg
from py_shared.domain import projects as pj

ADMIN_ID = "11111111-1111-1111-1111-111111111111"   # Principal — permissions admin
STAFF_ID = "22222222-2222-2222-2222-222222222222"   # Agent
MONDAY = date(2026, 7, 20)


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.user_roles')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.2 migration (0019) not applied")


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


@pytest.fixture()
def users(su: psycopg.Connection) -> Iterator[dict[str, str]]:
    """Two extra active users to assign work between, cleaned up afterward."""
    ids = {}
    for label in ("alice", "bob"):
        row = su.execute(
            "insert into app.os_users (id, email, display_name, is_active) "
            "values (gen_random_uuid(), %s, %s, true) returning id",
            (f"{label}-{uuid.uuid4().hex[:6]}@brunetco.com", label.title()),
        ).fetchone()
        ids[label] = str(row[0])
    yield ids
    # work_items -> os_users FK is NO ACTION, so clear the users' items before removing them
    # (escalations/reassignments cascade off work_items).
    su.execute(
        "delete from app.work_items where assignee_id = any(%s) or created_by = any(%s)",
        (list(ids.values()), list(ids.values())),
    )
    su.execute("delete from app.os_users where id = any(%s)", (list(ids.values()),))


def _work_item(su: psycopg.Connection, assignee: str | None, **kw: object) -> str:
    fields = {
        "title": "T", "created_by": ADMIN_ID, "assignee_id": assignee,
        "status": "open", "task_ref": "draft",
    }
    fields.update(kw)
    cols = ", ".join(fields)
    ph = ", ".join(["%s"] * len(fields))
    row = su.execute(
        f"insert into app.work_items ({cols}) values ({ph}) returning id", tuple(fields.values())
    ).fetchone()
    return str(row[0])


# --- capability pool + suggestion ----------------------------------------------


def test_suggestion_pool_is_empty_without_capability_registration(
    su: psycopg.Connection,
) -> None:
    assert asg.suggest_assignees(su, f"role-{uuid.uuid4().hex[:6]}") == []


def test_least_loaded_pool_member_is_suggested_first(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    role = f"drafter-{uuid.uuid4().hex[:6]}"
    su.execute("insert into app.user_roles (user_id, role) values (%s, %s), (%s, %s)",
               (users["alice"], role, users["bob"], role))
    # Alice already carries two open items; Bob none.
    _work_item(su, users["alice"])
    _work_item(su, users["alice"])
    try:
        ranked = asg.suggest_assignees(su, role)
        assert str(ranked[0].user_id) == users["bob"]
    finally:
        su.execute("delete from app.user_roles where role = %s", (role,))


def test_cycle_time_history_breaks_a_load_tie(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    """Equal load, so the faster historical cycle time on this task type decides it."""
    role = f"drafter-{uuid.uuid4().hex[:6]}"
    su.execute("insert into app.user_roles (user_id, role) values (%s, %s), (%s, %s)",
               (users["alice"], role, users["bob"], role))
    # A completed 'draft' each: Alice took 8 days, Bob 2.
    _work_item(su, users["alice"], status="done",
               started_on=MONDAY, completed_on=MONDAY + timedelta(days=8))
    _work_item(su, users["bob"], status="done",
               started_on=MONDAY, completed_on=MONDAY + timedelta(days=2))
    try:
        ranked = asg.suggest_assignees(su, role, task_ref="draft")
        assert str(ranked[0].user_id) == users["bob"]
    finally:
        su.execute("delete from app.user_roles where role = %s", (role,))


# --- launch fallback chain -----------------------------------------------------


def test_best_assignee_prefers_the_pool_over_the_single_default(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    role = f"drafter-{uuid.uuid4().hex[:6]}"
    su.execute("insert into app.user_roles (user_id, role) values (%s, %s)",
               (users["alice"], role))
    su.execute("insert into app.role_assignments (role, user_id) values (%s, %s)",
               (role, users["bob"]))
    try:
        assert str(asg.best_assignee(su, role)) == users["alice"]
    finally:
        su.execute("delete from app.user_roles where role = %s", (role,))
        su.execute("delete from app.role_assignments where role = %s", (role,))


def test_best_assignee_falls_back_to_the_default_when_no_pool(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    role = f"drafter-{uuid.uuid4().hex[:6]}"
    su.execute("insert into app.role_assignments (role, user_id) values (%s, %s)",
               (role, users["bob"]))
    try:
        assert str(asg.best_assignee(su, role)) == users["bob"]
    finally:
        su.execute("delete from app.role_assignments where role = %s", (role,))


def test_best_assignee_is_none_for_an_unstaffed_role(su: psycopg.Connection) -> None:
    assert asg.best_assignee(su, f"ghost-{uuid.uuid4().hex[:6]}") is None


def test_project_launch_now_routes_by_workload(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    """The WP 5.1 seam, delivered: launch routes to the least-loaded pool member, not one fixed
    person. Both users can draft; Bob is idle, Alice is buried, so the draft task goes to Bob."""
    role = f"drafter-{uuid.uuid4().hex[:6]}"
    su.execute("insert into app.user_roles (user_id, role) values (%s, %s), (%s, %s)",
               (users["alice"], role, users["bob"], role))
    for _ in range(3):
        _work_item(su, users["alice"])

    key = f"tpl-{uuid.uuid4().hex[:6]}"
    tmpl = su.execute(
        "insert into app.project_templates (key, version, name, created_by) "
        "values (%s, 1, 'T', %s) returning id",
        (key, ADMIN_ID),
    ).fetchone()[0]
    su.execute(
        "insert into app.project_template_tasks (template_id, task_ref, title, role, cycle_days) "
        "values (%s, 'draft', 'Draft', %s, 3)",
        (tmpl, role),
    )
    try:
        pj.publish_template(su, uuid.UUID(str(tmpl)))
        project_id = pj.launch_project(su, uuid.UUID(str(tmpl)), "P", uuid.UUID(ADMIN_ID), MONDAY)
        row = su.execute(
            "select assignee_id from app.work_items where project_id = %s and task_ref = 'draft'",
            (project_id,),
        ).fetchone()
        assert str(row[0]) == users["bob"]
    finally:
        su.execute("delete from app.project_templates where key = %s", (key,))
        su.execute("delete from app.user_roles where role = %s", (role,))


# --- capacity board ------------------------------------------------------------


def test_capacity_board_counts_load_overdue_and_due_soon(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    _work_item(su, users["alice"], due_date=date.today() - timedelta(days=2))   # overdue
    _work_item(su, users["alice"], due_date=date.today() + timedelta(days=3))   # due soon
    _work_item(su, users["alice"], status="done")                              # excluded
    row = su.execute(
        "select open_load, overdue, due_soon from app.capacity_board where user_id = %s",
        (users["alice"],),
    ).fetchone()
    assert row == (2, 1, 1)


# --- reassignment --------------------------------------------------------------


def test_reassignment_moves_the_item_and_logs_it(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    item = _work_item(su, users["alice"])
    asg.reassign_work_item(su, uuid.UUID(item), uuid.UUID(users["bob"]),
                           uuid.UUID(ADMIN_ID), reason="rebalancing")
    who = su.execute("select assignee_id from app.work_items where id = %s", (item,)).fetchone()
    assert str(who[0]) == users["bob"]
    log = su.execute(
        "select from_user_id, to_user_id, reason from app.work_item_reassignments "
        " where work_item_id = %s",
        (item,),
    ).fetchone()
    assert str(log[0]) == users["alice"] and str(log[1]) == users["bob"]
    assert log[2] == "rebalancing"


def test_parking_an_item_records_an_unassignment(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    item = _work_item(su, users["alice"])
    asg.reassign_work_item(su, uuid.UUID(item), None, uuid.UUID(ADMIN_ID))
    who = su.execute("select assignee_id from app.work_items where id = %s", (item,)).fetchone()
    assert who[0] is None


# --- SLA escalation ------------------------------------------------------------


@pytest.fixture()
def matter(su: psycopg.Connection, users: dict[str, str]) -> Iterator[str]:
    """A matter with Alice as the responsible professional — the escalation target."""
    cid = su.execute(
        "insert into app.clients (code, name) values (%s, 'Cap Co') returning id",
        (f"C{uuid.uuid4().hex[:5].upper()}",),
    ).fetchone()[0]
    fid = su.execute(
        "insert into app.families (client_id, family_seq, reference, title, family_type) "
        "values (%s, '0001', %s, 'F', 'patent') returning id",
        (cid, f"F{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    mid = su.execute(
        "insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment, "
        "status, responsible_user_id) values (%s, %s, 'CA', 'CA', 'pending', %s) returning id",
        (fid, f"M{uuid.uuid4().hex[:5]}", users["alice"]),
    ).fetchone()[0]
    yield str(mid)
    # NO ACTION FKs up the chain — tear down child-first.
    su.execute("delete from app.work_items where matter_id = %s", (mid,))
    su.execute("delete from app.matters where id = %s", (mid,))
    su.execute("delete from app.families where id = %s", (fid,))
    su.execute("delete from app.clients where id = %s", (cid,))


def test_sweep_escalates_an_overdue_item_to_the_responsible_professional(
    su: psycopg.Connection, users: dict[str, str], matter: str,
) -> None:
    item = _work_item(su, users["bob"], matter_id=matter,
                      due_date=date.today() - timedelta(days=1))
    opened = asg.sweep_sla_breaches(su)
    mine = [e for e in opened if str(e.work_item_id) == item]
    assert len(mine) == 1
    assert str(mine[0].escalated_to) == users["alice"]   # the matter's responsible professional
    assert mine[0].reason == "sla_breach"


def test_sweep_is_idempotent(su: psycopg.Connection, users: dict[str, str], matter: str) -> None:
    """Re-running must not stack duplicate escalations on the same breach."""
    _work_item(su, users["bob"], matter_id=matter, due_date=date.today() - timedelta(days=1))
    first = len(asg.sweep_sla_breaches(su))
    second = len(asg.sweep_sla_breaches(su))
    assert first >= 1 and second == 0


def test_an_unassigned_overdue_item_is_tagged_distinctly(
    su: psycopg.Connection, matter: str,
) -> None:
    item = _work_item(su, None, matter_id=matter, due_date=date.today() - timedelta(days=1))
    opened = asg.sweep_sla_breaches(su)
    mine = next(e for e in opened if str(e.work_item_id) == item)
    assert mine.reason == "unassigned_overdue"


def test_a_future_due_item_does_not_escalate(
    su: psycopg.Connection, users: dict[str, str], matter: str,
) -> None:
    item = _work_item(su, users["bob"], matter_id=matter,
                      due_date=date.today() + timedelta(days=5))
    opened = asg.sweep_sla_breaches(su)
    assert all(str(e.work_item_id) != item for e in opened)


def test_resolving_an_escalation_lets_a_later_breach_reescalate(
    su: psycopg.Connection, users: dict[str, str], matter: str,
) -> None:
    """Resolved rows are unconstrained, so the same item breaching again raises a fresh one —
    history is kept, not overwritten."""
    item = _work_item(su, users["bob"], matter_id=matter,
                      due_date=date.today() - timedelta(days=1))
    asg.sweep_sla_breaches(su)
    esc_id = su.execute(
        "select id from app.escalations where work_item_id = %s", (item,)
    ).fetchone()[0]
    asg.resolve_escalation(su, uuid.UUID(str(esc_id)))
    reopened = asg.sweep_sla_breaches(su)
    assert any(str(e.work_item_id) == item for e in reopened)


# --- RLS -----------------------------------------------------------------------


def test_capability_pool_edits_are_admin_gated(
    su: psycopg.Connection, users: dict[str, str],
) -> None:
    """The pool decides who the engine hands work to — a floor user cannot edit it."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _user_conn(STAFF_ID) as conn:
        conn.execute("insert into app.user_roles (user_id, role) values (%s, 'rogue')",
                     (users["alice"],))


def test_staff_can_read_the_capacity_board(su: psycopg.Connection) -> None:
    with _user_conn(STAFF_ID) as conn:
        conn.execute("select count(*) from app.capacity_board").fetchone()
