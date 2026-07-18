"""Time entry / flat-fee schema against Postgres (WPs 2.5.1–2.5.3, D42/D43).

The DB-level guarantees, asserted at the DB. Each one exists because the application layer is one
deleted line away from not enforcing it, and the data in question decides what people are paid:

  * post-invoice immutability (M2-R10)
  * attribution totalling exactly 100% (M2-R13)
  * the mandatory work-item link (M2-R11)
  * own-record visibility and cross-timekeeper isolation (D43)

Seed identities: ADMIN (1111…) is a Principal — every permission domain. STAFF (2222…) is an
Agent — time_entry and expense_entry only, no accounting_reporting. That asymmetry is the fixture.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from py_shared.config import settings

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.time_entries')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP2.5 migration (0016) not applied")


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    """RLS-scoped connection. Stays a context manager because the JWT claims are set with
    `set_config(..., true)` — transaction-local, so the identity dies with the block."""
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def work_item(su: psycopg.Connection) -> str:
    """A firm-general work item (no matter), so these tests exercise the time/permission rules
    without dragging family-ACL visibility into every assertion."""
    row = su.execute(
        "insert into app.work_items (title, created_by) values (%s, %s) returning id",
        (f"WP2.5 test {uuid.uuid4().hex[:6]}", ADMIN_ID),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _entry(su: psycopg.Connection, work_item: str, timekeeper: str, **kw: object) -> str:
    fields = {
        "timekeeper_id": timekeeper,
        "work_item_id": work_item,
        "entry_date": "2026-07-18",
        "minutes": 90,
        "rate_cents": 45_000,
        "narrative": "Drafting",
    }
    fields.update(kw)
    cols = ", ".join(fields)
    placeholders = ", ".join(["%s"] * len(fields))
    row = su.execute(
        f"insert into app.time_entries ({cols}) values ({placeholders}) returning id",
        tuple(fields.values()),
    ).fetchone()
    assert row is not None
    return str(row[0])


# --- structural guarantees -----------------------------------------------------


def test_time_entry_without_a_work_item_is_refused(su: psycopg.Connection) -> None:
    """M2-R11: matter-only time cannot be measured per piece of work, and back-filling the link
    across history means re-keying by hand."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        su.execute(
            "insert into app.time_entries (timekeeper_id, entry_date, minutes) "
            "values (%s, '2026-07-18', 60)",
            (ADMIN_ID,),
        )


def test_zero_or_negative_minutes_are_refused(su: psycopg.Connection, work_item: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _entry(su, work_item, ADMIN_ID, minutes=0)


def test_an_invoiced_entry_cannot_be_edited(su: psycopg.Connection, work_item: str) -> None:
    """Editable until invoiced, immutable after: a billed entry is a statement already made to a
    client. Corrections go through reason-coded credits (M2-R10)."""
    entry = _entry(su, work_item, ADMIN_ID, status="invoiced", invoice_line_id=str(uuid.uuid4()))
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        su.execute("update app.time_entries set minutes = 120 where id = %s", (entry,))


def test_an_invoiced_entry_cannot_be_deleted(su: psycopg.Connection, work_item: str) -> None:
    entry = _entry(su, work_item, ADMIN_ID, status="invoiced", invoice_line_id=str(uuid.uuid4()))
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be deleted"):
        su.execute("delete from app.time_entries where id = %s", (entry,))


def test_attaching_the_invoice_line_is_the_one_permitted_post_invoice_write(
    su: psycopg.Connection, work_item: str,
) -> None:
    """WP 3.2 stamps the linkage as it invoices; without this exemption the trigger would block
    the very write that marks the entry billed."""
    entry = _entry(su, work_item, ADMIN_ID, status="invoiced")
    su.execute(
        "update app.time_entries set invoice_line_id = %s where id = %s",
        (str(uuid.uuid4()), entry),
    )


def test_a_draft_entry_is_freely_editable(su: psycopg.Connection, work_item: str) -> None:
    entry = _entry(su, work_item, ADMIN_ID)
    su.execute("update app.time_entries set minutes = 120 where id = %s", (entry,))
    row = su.execute("select minutes from app.time_entries where id = %s", (entry,)).fetchone()
    assert row is not None and row[0] == 120


# --- flat-fee attribution ------------------------------------------------------


@pytest.fixture()
def flat_fee(su: psycopg.Connection, work_item: str) -> str:
    service = su.execute(
        "insert into app.flat_fee_services (code, name, amount_cents) values (%s, %s, %s) "
        "returning id",
        (f"FF{uuid.uuid4().hex[:6].upper()}", "Filing package", 200_000),
    ).fetchone()
    assert service is not None
    row = su.execute(
        "insert into app.flat_fee_items (service_id, work_item_id, amount_cents, entry_date, "
        "created_by) values (%s, %s, %s, '2026-07-18', %s) returning id",
        (service[0], work_item, 200_000, ADMIN_ID),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_a_split_not_totalling_100_percent_is_refused(
    su: psycopg.Connection, flat_fee: str,
) -> None:
    """9000 bps silently underpays someone and surfaces a quarter later at payroll — so the DB
    refuses it, not just a validation message."""
    with pytest.raises(psycopg.errors.RaiseException, match="10000 bps"):
        with psycopg.connect(settings.supabase_db_url) as c:
            c.execute(
                "insert into app.flat_fee_attributions (flat_fee_item_id, timekeeper_id, "
                "share_bps) values (%s, %s, 9000)",
                (flat_fee, ADMIN_ID),
            )
            c.commit()


def test_a_split_totalling_100_percent_is_accepted(
    su: psycopg.Connection, flat_fee: str,
) -> None:
    with psycopg.connect(settings.supabase_db_url) as c:
        c.execute(
            "insert into app.flat_fee_attributions (flat_fee_item_id, timekeeper_id, share_bps) "
            "values (%s, %s, 6000), (%s, %s, 4000)",
            (flat_fee, ADMIN_ID, flat_fee, STAFF_ID),
        )
        c.commit()
    row = su.execute(
        "select sum(share_bps) from app.flat_fee_attributions where flat_fee_item_id = %s",
        (flat_fee,),
    ).fetchone()
    assert row is not None and row[0] == 10_000


def test_a_multi_row_rewrite_is_not_rejected_midway(
    su: psycopg.Connection, flat_fee: str,
) -> None:
    """Deferred constraint: replacing a 60/40 with a 50/50 passes through an invalid intermediate
    state, which an immediate trigger would reject."""
    with psycopg.connect(settings.supabase_db_url) as c:
        c.execute(
            "insert into app.flat_fee_attributions (flat_fee_item_id, timekeeper_id, share_bps) "
            "values (%s, %s, 6000), (%s, %s, 4000)",
            (flat_fee, ADMIN_ID, flat_fee, STAFF_ID),
        )
        c.commit()
    with psycopg.connect(settings.supabase_db_url) as c:
        c.execute("delete from app.flat_fee_attributions where flat_fee_item_id = %s", (flat_fee,))
        c.execute(
            "insert into app.flat_fee_attributions (flat_fee_item_id, timekeeper_id, share_bps) "
            "values (%s, %s, 5000), (%s, %s, 5000)",
            (flat_fee, ADMIN_ID, flat_fee, STAFF_ID),
        )
        c.commit()


# --- production view -----------------------------------------------------------


def test_entered_and_billed_are_both_retained(su: psycopg.Connection, work_item: str) -> None:
    """Overwriting entered with billed destroys the only evidence of a write-down (M2-R10)."""
    _entry(su, work_item, ADMIN_ID)                     # 90 min @ $450/h = $675, draft
    _entry(su, work_item, ADMIN_ID, status="invoiced")  # same again, invoiced

    agg = su.execute(
        "select sum(entered_cents), sum(billed_cents) from app.timekeeper_production "
        "where work_item_id = %s",
        (work_item,),
    ).fetchone()
    assert agg is not None
    assert agg[0] == 135_000   # both entries entered
    assert agg[1] == 67_500    # only the invoiced one billed


def test_non_billable_time_is_excluded_from_production(
    su: psycopg.Connection, work_item: str,
) -> None:
    _entry(su, work_item, ADMIN_ID, is_billable=False)
    row = su.execute(
        "select coalesce(sum(entered_cents), 0) from app.timekeeper_production "
        "where work_item_id = %s",
        (work_item,),
    ).fetchone()
    assert row is not None and row[0] == 0


def test_collected_is_zero_until_phase_3_populates_it(
    su: psycopg.Connection, work_item: str,
) -> None:
    _entry(su, work_item, ADMIN_ID, status="invoiced")
    row = su.execute(
        "select sum(collected_cents) from app.timekeeper_production where work_item_id = %s",
        (work_item,),
    ).fetchone()
    assert row is not None and row[0] == 0


# --- D43 permissions + own-record rule (RLS, direct Postgres) ------------------


def test_a_timekeeper_cannot_see_another_timekeepers_time(
    su: psycopg.Connection, work_item: str,
) -> None:
    """Cross-timekeeper isolation. STAFF is an Agent — no accounting_reporting grant."""
    entry = _entry(su, work_item, ADMIN_ID)
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.time_entries where id = %s", (entry,)
        ).fetchone()
        assert row is not None and row[0] == 0


def test_accounting_reporting_sees_everyones_time(
    su: psycopg.Connection, work_item: str,
) -> None:
    """The grant is what distinguishes firm-wide reporting from own-record access (D43)."""
    entry = _entry(su, work_item, STAFF_ID)
    with _user_conn(ADMIN_ID) as conn:
        row = conn.execute(
            "select count(*) from app.time_entries where id = %s", (entry,)
        ).fetchone()
        assert row is not None and row[0] == 1


def test_a_user_sees_their_own_time_without_any_reporting_grant(
    su: psycopg.Connection, work_item: str,
) -> None:
    entry = _entry(su, work_item, STAFF_ID)
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.time_entries where id = %s", (entry,)
        ).fetchone()
        assert row is not None and row[0] == 1


def test_recording_time_as_another_timekeeper_is_refused(work_item: str) -> None:
    """Attributing work to someone else moves money between people's bonus bases."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _user_conn(STAFF_ID) as conn:
        conn.execute(
            "insert into app.time_entries (timekeeper_id, work_item_id, entry_date, minutes) "
            "values (%s, %s, '2026-07-18', 60)",
            (ADMIN_ID, work_item),
        )


def test_a_user_can_record_their_own_time(work_item: str) -> None:
    with _user_conn(STAFF_ID) as conn:
        conn.execute(
            "insert into app.time_entries (timekeeper_id, work_item_id, entry_date, minutes) "
            "values (%s, %s, '2026-07-18', 60)",
            (STAFF_ID, work_item),
        )
