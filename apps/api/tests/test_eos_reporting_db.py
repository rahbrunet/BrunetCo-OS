"""L10 pack, 1-on-1 reports, scorecard auto-population (WP 5.8, §M9) — against Postgres."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import eos_reporting as er
from py_shared.domain import micro_requests as mr

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
TODAY = date(2026, 7, 22)      # a Wednesday
MONDAY = date(2026, 7, 20)


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.scorecard_measurables')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.7/5.8 migrations not applied")


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def user(su: psycopg.Connection) -> Iterator[str]:
    """A fresh user so metrics are computed over this test's data alone."""
    uid = su.execute(
        "insert into app.os_users (id, email, display_name, is_active) "
        "values (gen_random_uuid(), %s, 'Metric User', true) returning id",
        (f"metric-{uuid.uuid4().hex[:6]}@brunetco.com",),
    ).fetchone()[0]
    yield str(uid)
    su.execute("delete from app.scorecard_measurables where owner_id = %s", (uid,))
    su.execute("delete from app.eos_todos where owner_id = %s", (uid,))
    su.execute("delete from app.micro_requests where assignee_id = %s", (uid,))
    su.execute("delete from app.work_items where assignee_id = %s", (uid,))
    su.execute("delete from app.os_users where id = %s", (uid,))


def _work_item(su: psycopg.Connection, **kw: object) -> str:
    fields = {"title": "T", "created_by": ADMIN_ID, "status": "open"}
    fields.update(kw)
    cols = ", ".join(fields)
    ph = ", ".join(["%s"] * len(fields))
    return str(su.execute(
        f"insert into app.work_items ({cols}) values ({ph}) returning id", tuple(fields.values())
    ).fetchone()[0])


# --- week anchoring ------------------------------------------------------------


def test_week_start_is_the_monday() -> None:
    """A Friday entry and the following Monday's review must refer to the same week."""
    assert er.week_start_for(date(2026, 7, 22)) == MONDAY   # Wednesday
    assert er.week_start_for(date(2026, 7, 24)) == MONDAY   # Friday
    assert er.week_start_for(MONDAY) == MONDAY


# --- metric computation --------------------------------------------------------


def test_on_time_completion_measures_done_work(su: psycopg.Connection, user: str) -> None:
    _work_item(su, assignee_id=user, status="done", due_date=date(2026, 7, 20),
               completed_on=date(2026, 7, 19))     # on time
    _work_item(su, assignee_id=user, status="done", due_date=date(2026, 7, 20),
               completed_on=date(2026, 7, 25))     # late
    value = er.compute_metric(su, er.ON_TIME_COMPLETION_PCT, uuid.UUID(user), TODAY)
    assert value == 50.0


def test_no_completed_work_is_no_data_not_zero(su: psycopg.Connection, user: str) -> None:
    """"Nothing happened" and "everything failed" are different weeks; a red zero would lie."""
    assert er.compute_metric(su, er.ON_TIME_COMPLETION_PCT, uuid.UUID(user), TODAY) is None


def test_overdue_count_is_a_real_zero(su: psycopg.Connection, user: str) -> None:
    """Zero overdue IS a measurement — distinct from no_data."""
    assert er.compute_metric(su, er.OVERDUE_OPEN_COUNT, uuid.UUID(user), TODAY) == 0.0


def test_overdue_count_counts_past_due_open_items(su: psycopg.Connection, user: str) -> None:
    _work_item(su, assignee_id=user, due_date=date(2026, 7, 1))     # overdue
    _work_item(su, assignee_id=user, due_date=date(2026, 8, 1))     # not yet
    assert er.compute_metric(su, er.OVERDUE_OPEN_COUNT, uuid.UUID(user), TODAY) == 1.0


def test_todo_completion_percentage(su: psycopg.Connection, user: str) -> None:
    for done in (True, True, False, False):
        su.execute(
            "insert into app.eos_todos (title, owner_id, done) values ('t', %s, %s)",
            (user, done),
        )
    assert er.compute_metric(su, er.TODO_COMPLETION_PCT, uuid.UUID(user), TODAY) == 50.0


def test_aging_wip_counts_only_old_open_items(su: psycopg.Connection, user: str) -> None:
    old = datetime.now().astimezone() - timedelta(days=er.AGING_WIP_DAYS + 5)
    su.execute(
        "insert into app.work_items (title, created_by, assignee_id, status, created_at) "
        "values ('old', %s, %s, 'open', %s)",
        (ADMIN_ID, user, old),
    )
    _work_item(su, assignee_id=user)   # fresh
    assert er.compute_metric(su, er.AGING_WIP_COUNT, uuid.UUID(user), TODAY) == 1.0


def test_micro_request_turnaround_averages_resolved(su: psycopg.Connection, user: str) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(user), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.resolve_request(su, rid, uuid.UUID(user))
    value = er.compute_metric(su, er.MICRO_REQUEST_TURNAROUND_HOURS, uuid.UUID(user), TODAY)
    assert value is not None and value >= 0


def test_an_unknown_metric_raises_rather_than_silently_skipping(
    su: psycopg.Connection, user: str,
) -> None:
    """A scorecard row that quietly stops updating still shows its last value, which reads as
    'the number held steady' when it means 'nobody is measuring this'."""
    with pytest.raises(er.UnknownMetric):
        er.compute_metric(su, "invented_metric", uuid.UUID(user), TODAY)


# --- auto-population -----------------------------------------------------------


def test_populate_writes_an_entry_for_the_current_week(
    su: psycopg.Connection, user: str,
) -> None:
    mid = su.execute(
        "insert into app.scorecard_measurables (name, owner_id, goal, direction, source_metric) "
        "values ('Overdue', %s, 0, 'lower_is_better', %s) returning id",
        (user, er.OVERDUE_OPEN_COUNT),
    ).fetchone()[0]
    _work_item(su, assignee_id=user, due_date=date(2026, 7, 1))

    er.populate_scorecard(su, as_of=TODAY)
    row = su.execute(
        "select week_start, value from app.scorecard_entries where measurable_id = %s", (mid,)
    ).fetchone()
    assert row[0] == MONDAY and row[1] == 1


def test_populate_is_idempotent_for_a_week(su: psycopg.Connection, user: str) -> None:
    """Runs nightly and on demand before an L10 — must overwrite, not append."""
    mid = su.execute(
        "insert into app.scorecard_measurables (name, owner_id, goal, source_metric) "
        "values ('Overdue', %s, 0, %s) returning id",
        (user, er.OVERDUE_OPEN_COUNT),
    ).fetchone()[0]
    er.populate_scorecard(su, as_of=TODAY)
    er.populate_scorecard(su, as_of=TODAY)
    n = su.execute(
        "select count(*) from app.scorecard_entries where measurable_id = %s", (mid,)
    ).fetchone()[0]
    assert n == 1


def test_populate_never_overwrites_a_manual_measurable(
    su: psycopg.Connection, user: str,
) -> None:
    """Some numbers genuinely come from outside the system; the job must not clobber them."""
    mid = su.execute(
        "insert into app.scorecard_measurables (name, owner_id, goal, source_metric) "
        "values ('Typed by hand', %s, 10, null) returning id",
        (user,),
    ).fetchone()[0]
    su.execute(
        "insert into app.scorecard_entries (measurable_id, week_start, value) values (%s, %s, 7)",
        (mid, MONDAY),
    )
    er.populate_scorecard(su, as_of=TODAY)
    value = su.execute(
        "select value from app.scorecard_entries where measurable_id = %s", (mid,)
    ).fetchone()[0]
    assert value == 7


def test_populate_reports_no_data_measurables(su: psycopg.Connection, user: str) -> None:
    su.execute(
        "insert into app.scorecard_measurables (name, owner_id, goal, source_metric) "
        "values ('On-time', %s, 90, %s)",
        (user, er.ON_TIME_COMPLETION_PCT),
    )
    result = er.populate_scorecard(su, as_of=TODAY)
    assert result["no_data"] >= 1


# --- L10 pack ------------------------------------------------------------------


def test_l10_pack_assembles_every_section(su: psycopg.Connection, user: str) -> None:
    quarter = f"2026-Q{uuid.uuid4().hex[:4]}"
    su.execute(
        "insert into app.rocks (title, owner_id, quarter, status) "
        "values ('Rock', %s, %s, 'off_track')",
        (user, quarter),
    )
    try:
        pack = er.l10_pack(su, quarter, as_of=TODAY)
        assert pack.week_start == MONDAY
        assert pack.rocks.get("off_track") == 1
        assert "committed" in pack.todo_completion
        assert isinstance(pack.critical_deadlines, list)
        assert isinstance(pack.aging_wip, list)
    finally:
        su.execute("delete from app.rocks where quarter = %s", (quarter,))


def test_headline_counts_reds_and_off_track_rocks(su: psycopg.Connection, user: str) -> None:
    """The four numbers that open the meeting."""
    quarter = f"2026-Q{uuid.uuid4().hex[:4]}"
    su.execute(
        "insert into app.rocks (title, owner_id, quarter, status) "
        "values ('R', %s, %s, 'off_track')",
        (user, quarter),
    )
    try:
        pack = er.l10_pack(su, quarter, as_of=TODAY)
        assert pack.headline["rocks_off_track"] == 1
        assert "scorecard_red" in pack.headline
        assert "deadlines_next_7_days" in pack.headline
    finally:
        su.execute("delete from app.rocks where quarter = %s", (quarter,))


def test_pack_deadlines_respect_the_seven_day_horizon(su: psycopg.Connection) -> None:
    pack = er.l10_pack(su, "2026-Q3", as_of=TODAY)
    horizon = TODAY + timedelta(days=er.DEADLINE_HORIZON_DAYS)
    for row in pack.critical_deadlines:
        assert TODAY <= row["final_due_date"] <= horizon


# --- 1-on-1 --------------------------------------------------------------------


def test_one_on_one_is_scoped_to_the_person(su: psycopg.Connection, user: str) -> None:
    """Seat-owned, not surveillance: the report contains nobody else's numbers."""
    quarter = f"2026-Q{uuid.uuid4().hex[:4]}"
    su.execute(
        "insert into app.rocks (title, owner_id, quarter) values ('Mine', %s, %s)",
        (user, quarter),
    )
    su.execute(
        "insert into app.rocks (title, owner_id, quarter) values ('Theirs', %s, %s)",
        (ADMIN_ID, quarter),
    )
    try:
        report = er.one_on_one_report(su, uuid.UUID(user), quarter, as_of=TODAY)
        assert [r["title"] for r in report.rocks] == ["Mine"]
    finally:
        su.execute("delete from app.rocks where quarter = %s", (quarter,))


def test_one_on_one_carries_todo_score_and_open_items(
    su: psycopg.Connection, user: str,
) -> None:
    su.execute(
        "insert into app.eos_todos (title, owner_id, done) values ('t', %s, true)", (user,)
    )
    _work_item(su, assignee_id=user)
    report = er.one_on_one_report(su, uuid.UUID(user), "2026-Q3", as_of=TODAY)
    assert report.todo_score["committed"] == 1
    assert report.todo_score["rate_pct"] == 100.0
    assert len(report.open_items) == 1
