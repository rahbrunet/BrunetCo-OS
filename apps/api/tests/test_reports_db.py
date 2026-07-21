"""Report Builder against Postgres (WP 5B.1).

The test that carries the design promise is
`test_a_shared_report_shows_each_viewer_only_their_own_rows`: sharing shares the *definition*, and
the run happens on the viewer's connection, so a shared report can never surface a matter the
recipient could not already open.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import reports as rp

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.reports')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5B.1 migration (0027) not applied")


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
def matters(su: psycopg.Connection) -> Iterator[dict[str, str]]:
    """One open matter and one on a restricted family — the visibility difference under test."""
    tag = uuid.uuid4().hex[:8]
    client = su.execute(
        "insert into app.clients (code, name) values (%s, %s) returning id",
        (f"R{tag}", f"Report Client {tag}"),
    ).fetchone()[0]
    made: dict[str, str] = {"client": str(client)}
    for label, restricted, seq in (("open", False, 1), ("restricted", True, 2)):
        family = su.execute(
            "insert into app.families (client_id, family_seq, family_type, reference, title, "
            " restricted) values (%s, %s, 'patent', %s, 'Widget', %s) returning id",
            (client, seq, f"{tag}-{seq}", restricted),
        ).fetchone()[0]
        matter = su.execute(
            "insert into app.matters (family_id, reference, jurisdiction_code, "
            " jurisdiction_segment, status) values (%s, %s, 'CA', 'CA', 'filed') returning id",
            (family, f"{tag}{seq}CA"),
        ).fetchone()[0]
        made[f"{label}_family"] = str(family)
        made[f"{label}_matter"] = str(matter)
        made[f"{label}_reference"] = f"{tag}{seq}CA"
    yield made

    for label in ("open", "restricted"):
        su.execute("delete from app.matters where id = %s", (made[f"{label}_matter"],))
        su.execute("delete from app.families where id = %s", (made[f"{label}_family"],))
    su.execute("delete from app.clients where id = %s", (client,))


def _report(
    conn: psycopg.Connection, owner: str, reference_prefix: str, shared: bool = False,
) -> uuid.UUID:
    return rp.save_report(
        conn, uuid.UUID(owner), f"Matters {uuid.uuid4().hex[:6]}",
        rp.Definition(
            "matters", columns=["reference", "status"],
            filters=[{"column": "reference", "op": "contains", "value": reference_prefix}],
            sort=["reference"],
        ),
        shared=shared,
    )


# --- saving --------------------------------------------------------------------


def test_an_unrunnable_report_is_never_saved(su: psycopg.Connection) -> None:
    """Validation precedes the insert, so a report cannot be saved, shared, then fail on open."""
    before = su.execute("select count(*) from app.reports").fetchone()[0]
    with pytest.raises(rp.ReportDefinitionError):
        rp.save_report(su, uuid.UUID(ADMIN_ID), "bad", rp.Definition("matters", columns=["oops"]))
    assert su.execute("select count(*) from app.reports").fetchone()[0] == before


def test_an_unknown_schedule_frequency_is_refused(su: psycopg.Connection) -> None:
    with pytest.raises(rp.ReportDefinitionError, match="schedule frequency"):
        rp.save_report(su, uuid.UUID(ADMIN_ID), "hourly", rp.Definition(
            "matters", columns=["reference"]), schedule_frequency="hourly")


def test_a_definition_round_trips_through_the_database(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    rid = _report(su, ADMIN_ID, matters["open_reference"][:8])
    try:
        loaded = rp.load_definition(su, rid)
        assert loaded.dataset == "matters"
        assert loaded.columns == ["reference", "status"]
        assert loaded.filters[0]["op"] == "contains"
    finally:
        su.execute("delete from app.reports where id = %s", (rid,))


# --- running -------------------------------------------------------------------


def test_running_a_report_returns_rows_and_logs_the_run(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    rid = _report(su, ADMIN_ID, matters["open_reference"][:8])
    try:
        result = rp.run_report(su, rid, uuid.UUID(ADMIN_ID))
        assert result.columns == ["reference", "status"]
        assert {r["reference"] for r in result.rows} == {
            matters["open_reference"], matters["restricted_reference"],
        }
        log = rp.recent_runs(su, rid)
        assert log[0]["row_count"] == 2
        assert log[0]["status"] == "ok"
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_run_exports_as_a_spreadsheet(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    rid = _report(su, ADMIN_ID, matters["open_reference"][:8])
    try:
        csv_text = rp.to_spreadsheet(rp.run_report(su, rid, uuid.UUID(ADMIN_ID)))
        assert csv_text.splitlines()[0] == "reference,status"
        assert matters["open_reference"] in csv_text
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_grouped_report_runs_against_real_data(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    rid = rp.save_report(
        su, uuid.UUID(ADMIN_ID), f"By status {uuid.uuid4().hex[:6]}",
        rp.Definition(
            "matters", group_by=["status"], aggregates=[rp.Aggregate("count")],
            filters=[{"column": "reference", "op": "contains",
                      "value": matters["open_reference"][:8]}],
            sort=["-count"],
        ),
    )
    try:
        result = rp.run_report(su, rid, uuid.UUID(ADMIN_ID))
        assert result.columns == ["status", "count"]
        assert result.rows == [{"status": "filed", "count": 2}]
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_failed_run_is_recorded_not_swallowed(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    rid = _report(su, ADMIN_ID, matters["open_reference"][:8])
    try:
        rp.record_failure(su, rid, uuid.UUID(ADMIN_ID), "transport unavailable")
        log = rp.recent_runs(su, rid)
        assert log[0]["status"] == "failed"
        assert log[0]["error"] == "transport unavailable"
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


# --- sharing and RLS: the design promise ---------------------------------------


def test_a_shared_report_shows_each_viewer_only_their_own_rows(
    su: psycopg.Connection, matters: dict[str, str],
) -> None:
    """Sharing shares the definition, not the data. The restricted family's matter is in the
    admin's result and absent from the staff member's, from the same saved report."""
    prefix = matters["open_reference"][:8]
    with _user_conn(ADMIN_ID) as admin:
        rid = _report(admin, ADMIN_ID, prefix, shared=True)
        admin_rows = {r["reference"] for r in rp.run_report(admin, rid, uuid.UUID(ADMIN_ID)).rows}
    try:
        with _user_conn(STAFF_ID) as staff:
            staff_rows = {r["reference"] for r in rp.run_report(staff, rid,
                                                                uuid.UUID(STAFF_ID)).rows}
        assert matters["restricted_reference"] in admin_rows
        assert matters["restricted_reference"] not in staff_rows
        assert matters["open_reference"] in staff_rows
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_private_report_is_invisible_to_everyone_else(su: psycopg.Connection) -> None:
    with _user_conn(ADMIN_ID) as admin:
        rid = rp.save_report(admin, uuid.UUID(ADMIN_ID), f"Private {uuid.uuid4().hex[:6]}",
                             rp.Definition("matters", columns=["reference"]), shared=False)
    try:
        with _user_conn(STAFF_ID) as staff:
            assert not [r for r in rp.list_reports(staff) if str(r["id"]) == str(rid)]
            with pytest.raises(LookupError):
                rp.load_definition(staff, rid)
    finally:
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_shared_report_is_not_a_communal_one(su: psycopg.Connection) -> None:
    """A recipient can run it but must not be able to change what it means."""
    with _user_conn(ADMIN_ID) as admin:
        rid = rp.save_report(admin, uuid.UUID(ADMIN_ID), f"Shared {uuid.uuid4().hex[:6]}",
                             rp.Definition("matters", columns=["reference"]), shared=True)
    try:
        with _user_conn(STAFF_ID) as staff:
            assert [r for r in rp.list_reports(staff) if str(r["id"]) == str(rid)]
            staff.execute("update app.reports set name = 'hijacked' where id = %s", (rid,))
            assert staff.execute(
                "select count(*) from app.reports where id = %s and name = 'hijacked'", (rid,),
            ).fetchone()[0] == 0
    finally:
        su.execute("delete from app.reports where id = %s", (rid,))


def test_a_run_cannot_be_logged_in_someone_elses_name(su: psycopg.Connection) -> None:
    with _user_conn(ADMIN_ID) as admin:
        rid = rp.save_report(admin, uuid.UUID(ADMIN_ID), f"Shared {uuid.uuid4().hex[:6]}",
                             rp.Definition("matters", columns=["reference"]), shared=True)
    try:
        with _user_conn(STAFF_ID) as staff, pytest.raises(psycopg.errors.Error):
            staff.execute(
                "insert into app.report_runs (report_id, requested_by, row_count) "
                "values (%s, %s, 0)", (rid, ADMIN_ID),
            )
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


# --- scheduling ----------------------------------------------------------------


def test_a_scheduled_report_appears_in_the_due_list(su: psycopg.Connection) -> None:
    from datetime import datetime

    rid = rp.save_report(
        su, uuid.UUID(ADMIN_ID), f"Weekly {uuid.uuid4().hex[:6]}",
        rp.Definition("matters", columns=["reference"]),
        schedule_frequency="weekly", schedule_hour=7,
    )
    try:
        assert rid in rp.due_reports(su, now=datetime(2026, 7, 20, 8, 0).astimezone())
        rp.run_report(su, rid, uuid.UUID(ADMIN_ID))
        assert rid not in rp.due_reports(su, now=datetime.now().astimezone())
    finally:
        su.execute("delete from app.report_runs where report_id = %s", (rid,))
        su.execute("delete from app.reports where id = %s", (rid,))


def test_an_unscheduled_report_is_never_in_the_due_list(su: psycopg.Connection) -> None:
    rid = rp.save_report(su, uuid.UUID(ADMIN_ID), f"Adhoc {uuid.uuid4().hex[:6]}",
                         rp.Definition("matters", columns=["reference"]))
    try:
        assert rid not in rp.due_reports(su)
    finally:
        su.execute("delete from app.reports where id = %s", (rid,))
