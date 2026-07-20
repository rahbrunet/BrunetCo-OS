"""Board membership, field values, and the automation engine against Postgres (WP 5.3, §M9).

The automation end-to-end tests are the point: a no-code rule that says "when this is filed,
create an invoice-review task" must actually create that task, on the same matter, and leave a
run-log entry explaining why it appeared.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import boards

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.boards')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.3 migration (0020) not applied")


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


def _work_item(su: psycopg.Connection, **kw: object) -> str:
    fields = {"title": "T", "created_by": ADMIN_ID, "status": "open"}
    fields.update(kw)
    cols = ", ".join(fields)
    ph = ", ".join(["%s"] * len(fields))
    row = su.execute(
        f"insert into app.work_items ({cols}) values ({ph}) returning id", tuple(fields.values())
    ).fetchone()
    return str(row[0])


@pytest.fixture()
def firm_board(su: psycopg.Connection) -> Iterator[str]:
    row = su.execute(
        "insert into app.boards (name, scope_type, created_by) values ('All work', 'firm', %s) "
        "returning id",
        (ADMIN_ID,),
    ).fetchone()
    yield str(row[0])
    su.execute("delete from app.boards where id = %s", (row[0],))


# --- typed field values --------------------------------------------------------


def test_a_valid_custom_value_is_stored(su: psycopg.Connection, firm_board: str) -> None:
    col = su.execute(
        "insert into app.board_columns (board_id, key, label, col_type) "
        "values (%s, 'pages', 'Est. pages', 'number') returning id",
        (firm_board,),
    ).fetchone()[0]
    item = _work_item(su)
    boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), 12)
    stored = su.execute(
        "select value from app.work_item_field_values where work_item_id = %s and column_id = %s",
        (item, col),
    ).fetchone()
    assert stored[0] == 12


def test_a_mistyped_custom_value_is_refused(su: psycopg.Connection, firm_board: str) -> None:
    col = su.execute(
        "insert into app.board_columns (board_id, key, label, col_type) "
        "values (%s, 'pages', 'Est. pages', 'number') returning id",
        (firm_board,),
    ).fetchone()[0]
    item = _work_item(su)
    with pytest.raises(boards.FieldValueInvalid):
        boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), "many")


def test_setting_a_value_on_a_builtin_column_is_refused(
    su: psycopg.Connection, firm_board: str,
) -> None:
    """Builtin columns read through to the work item; they hold no stored value."""
    col = su.execute(
        "insert into app.board_columns (board_id, key, label, col_type, is_builtin) "
        "values (%s, 'status', 'Status', 'status', true) returning id",
        (firm_board,),
    ).fetchone()[0]
    item = _work_item(su)
    with pytest.raises(ValueError, match="builtin"):
        boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), "done")


def test_upsert_overwrites_the_previous_value(su: psycopg.Connection, firm_board: str) -> None:
    col = su.execute(
        "insert into app.board_columns (board_id, key, label, col_type) "
        "values (%s, 'pages', 'P', 'number') returning id",
        (firm_board,),
    ).fetchone()[0]
    item = _work_item(su)
    boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), 5)
    boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), 8)
    rows = su.execute(
        "select value from app.work_item_field_values where work_item_id = %s and column_id = %s",
        (item, col),
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == 8


# --- membership ----------------------------------------------------------------


def test_firm_board_shows_all_work_items(su: psycopg.Connection, firm_board: str) -> None:
    item = _work_item(su)
    assert uuid.UUID(item) in boards.board_work_item_ids(su, uuid.UUID(firm_board))


def test_project_board_shows_only_its_projects_items(su: psycopg.Connection) -> None:
    proj = su.execute(
        "insert into app.projects (name, created_by) values ('P', %s) returning id", (ADMIN_ID,)
    ).fetchone()[0]
    board = su.execute(
        "insert into app.boards (name, scope_type, scope_id, created_by) "
        "values ('Proj', 'project', %s, %s) returning id",
        (proj, ADMIN_ID),
    ).fetchone()[0]
    try:
        on_project = _work_item(su, project_id=proj)
        off_project = _work_item(su)
        ids = boards.board_work_item_ids(su, uuid.UUID(str(board)))
        assert uuid.UUID(on_project) in ids
        assert uuid.UUID(off_project) not in ids
    finally:
        su.execute("delete from app.boards where id = %s", (board,))
        su.execute("delete from app.work_items where project_id = %s", (proj,))
        su.execute("delete from app.projects where id = %s", (proj,))


# --- automation engine ---------------------------------------------------------


def _automation(su: psycopg.Connection, board_id: str, trigger: dict, actions: list) -> str:
    boards.validate_automation(trigger, actions)
    row = su.execute(
        "insert into app.board_automations (board_id, name, trigger, actions, created_by) "
        "values (%s, 'auto', %s, %s, %s) returning id",
        (board_id, json.dumps(trigger), json.dumps(actions), ADMIN_ID),
    ).fetchone()
    return str(row[0])


def test_a_matching_automation_creates_a_task_on_the_same_matter(
    su: psycopg.Connection, firm_board: str,
) -> None:
    """The spec's headline example: when status -> done, create a follow-up task — inheriting the
    triggering item's matter so the new work lands on the same file."""
    _automation(
        su, firm_board,
        {"event": "status_changed", "to": "done"},
        [{"type": "create_task", "title": "Invoice review"}],
    )
    item = _work_item(su)
    runs = boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item),
        {"type": "status_changed", "to": "done"},
    )
    assert len(runs) == 1
    created = su.execute(
        "select title from app.work_items where title = 'Invoice review'"
    ).fetchone()
    assert created is not None


def test_a_non_matching_event_fires_nothing(
    su: psycopg.Connection, firm_board: str,
) -> None:
    _automation(
        su, firm_board,
        {"event": "status_changed", "to": "done"},
        [{"type": "create_task", "title": "Should not appear"}],
    )
    item = _work_item(su)
    runs = boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item),
        {"type": "status_changed", "to": "in_progress"},
    )
    assert runs == []
    assert su.execute(
        "select count(*) from app.work_items where title = 'Should not appear'"
    ).fetchone()[0] == 0


def test_the_run_log_records_what_the_automation_did(
    su: psycopg.Connection, firm_board: str,
) -> None:
    """"Why did this task appear?" must trace to the rule that caused it."""
    auto_id = _automation(
        su, firm_board,
        {"event": "status_changed"},
        [{"type": "create_task", "title": "Traced task"}],
    )
    item = _work_item(su)
    boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item), {"type": "status_changed", "to": "done"}
    )
    run = su.execute(
        "select actions_taken from app.board_automation_runs where automation_id = %s", (auto_id,)
    ).fetchone()
    assert run is not None
    assert run[0][0]["type"] == "create_task"
    assert "created_work_item" in run[0][0]


def test_notify_records_the_intended_recipient(
    su: psycopg.Connection, firm_board: str,
) -> None:
    """No delivery channel yet (Teams is 5.4); the intent is logged so it is auditable now."""
    _automation(
        su, firm_board,
        {"event": "status_changed", "to": "done"},
        [{"type": "notify", "target": "owner"}],
    )
    item = _work_item(su, assignee_id=STAFF_ID)
    runs = boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item), {"type": "status_changed", "to": "done"}
    )
    run = su.execute(
        "select actions_taken from app.board_automation_runs where id = %s", (runs[0],)
    ).fetchone()
    assert run[0][0]["recipient"] == STAFF_ID


def test_set_status_action_mutates_the_item(
    su: psycopg.Connection, firm_board: str,
) -> None:
    _automation(
        su, firm_board,
        {"event": "field_changed", "column": "approved", "to": True},
        [{"type": "set_status", "to": "done"}],
    )
    item = _work_item(su)
    boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item),
        {"type": "field_changed", "column": "approved", "to": True},
    )
    status = su.execute("select status from app.work_items where id = %s", (item,)).fetchone()
    assert status[0] == "done"


def test_a_disabled_automation_does_not_fire(
    su: psycopg.Connection, firm_board: str,
) -> None:
    auto_id = _automation(
        su, firm_board,
        {"event": "status_changed"},
        [{"type": "create_task", "title": "Disabled task"}],
    )
    su.execute("update app.board_automations set enabled = false where id = %s", (auto_id,))
    item = _work_item(su)
    runs = boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item), {"type": "status_changed", "to": "done"}
    )
    assert runs == []


def test_one_broken_action_does_not_silence_the_run(
    su: psycopg.Connection, firm_board: str,
) -> None:
    """A set_status to a value the check constraint rejects fails the action — but the run is
    still logged with the failure, rather than the whole batch aborting."""
    _automation(
        su, firm_board,
        {"event": "status_changed", "to": "done"},
        [{"type": "set_status", "to": "not_a_real_status"}],
    )
    item = _work_item(su)
    runs = boards.run_automations(
        su, uuid.UUID(firm_board), uuid.UUID(item), {"type": "status_changed", "to": "done"}
    )
    assert len(runs) == 1
    detail = su.execute(
        "select detail from app.board_automation_runs where id = %s", (runs[0],)
    ).fetchone()
    assert detail[0] is not None and "failed" in detail[0]


# --- RLS -----------------------------------------------------------------------


def test_staff_can_read_a_firm_board(su: psycopg.Connection, firm_board: str) -> None:
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.boards where id = %s", (firm_board,)
        ).fetchone()
        assert row[0] == 1


def test_a_field_value_on_a_restricted_matter_is_hidden(su: psycopg.Connection) -> None:
    """A custom column value is as sensitive as the item it annotates: a restricted matter's
    field value must not leak through a board."""
    cid = su.execute(
        "insert into app.clients (code, name) values (%s, 'B Co') returning id",
        (f"B{uuid.uuid4().hex[:5].upper()}",),
    ).fetchone()[0]
    fid = su.execute(
        "insert into app.families (client_id, family_seq, reference, title, family_type, "
        "restricted) values (%s, '0001', %s, 'Secret', 'patent', true) returning id",
        (cid, f"S{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    mid = su.execute(
        "insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment, "
        "status) values (%s, %s, 'CA', 'CA', 'pending') returning id",
        (fid, f"M{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    board = su.execute(
        "insert into app.boards (name, scope_type, scope_id, created_by) "
        "values ('Secret board', 'matter', %s, %s) returning id",
        (mid, ADMIN_ID),
    ).fetchone()[0]
    col = su.execute(
        "insert into app.board_columns (board_id, key, label, col_type) "
        "values (%s, 'note', 'Note', 'text') returning id",
        (board,),
    ).fetchone()[0]
    item = _work_item(su, matter_id=mid)
    boards.set_field_value(su, uuid.UUID(item), uuid.UUID(str(col)), "confidential")
    try:
        with _user_conn(STAFF_ID) as conn:
            # STAFF is not on the restricted family's ACL, so neither the board nor the value.
            n = conn.execute(
                "select count(*) from app.work_item_field_values where work_item_id = %s", (item,)
            ).fetchone()
            assert n[0] == 0
    finally:
        su.execute("delete from app.boards where id = %s", (board,))
        su.execute("delete from app.work_items where matter_id = %s", (mid,))
        su.execute("delete from app.matters where id = %s", (mid,))
        su.execute("delete from app.families where id = %s", (fid,))
        su.execute("delete from app.clients where id = %s", (cid,))
