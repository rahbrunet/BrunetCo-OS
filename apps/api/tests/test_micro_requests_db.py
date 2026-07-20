"""Micro-requests against Postgres (WP 5.4, §M9).

The parent-blocking test is the one that carries the design promise: "I sent it for review" and
"it was actually reviewed" become the same fact, because the parent cannot be completed while the
request is open.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import micro_requests as mr
from py_shared.domain import projects as pj

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.micro_requests')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.4 migration (0021) not applied")


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
    return str(su.execute(
        f"insert into app.work_items ({cols}) values ({ph}) returning id", tuple(fields.values())
    ).fetchone()[0])


# --- creation ------------------------------------------------------------------


def test_a_request_needs_exactly_one_parent(su: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="exactly one parent"):
        mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review please")


def test_an_empty_prompt_is_refused(su: psycopg.Connection) -> None:
    item = _work_item(su)
    with pytest.raises(ValueError, match="prompt"):
        mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "  ",
                          parent_work_item_id=uuid.UUID(item))


def test_creating_a_request_sets_an_sla(su: psycopg.Connection) -> None:
    item = _work_item(su)
    now = datetime(2026, 7, 20, 9, 0).astimezone()
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item), sla_hours=4, now=now)
    due = su.execute("select sla_due from app.micro_requests where id = %s", (rid,)).fetchone()[0]
    assert due == now + timedelta(hours=4)


# --- parent blocking (the design promise) --------------------------------------


def test_an_open_request_blocks_its_parent(su: psycopg.Connection) -> None:
    item = _work_item(su)
    mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review before filing",
                      parent_work_item_id=uuid.UUID(item))
    blocked = su.execute("select app.work_item_is_blocked(%s)", (item,)).fetchone()[0]
    assert blocked is True


def test_a_blocked_parent_cannot_be_completed(su: psycopg.Connection) -> None:
    item = _work_item(su)
    mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                      parent_work_item_id=uuid.UUID(item))
    with pytest.raises(ValueError, match="blocked"):
        pj.complete_work_item(su, uuid.UUID(item))


def test_resolving_the_request_unblocks_the_parent(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.resolve_request(su, rid, uuid.UUID(STAFF_ID))
    assert su.execute("select app.work_item_is_blocked(%s)", (item,)).fetchone()[0] is False
    # And now it can complete.
    pj.complete_work_item(su, uuid.UUID(item))


# --- state machine + round-trips -----------------------------------------------


def test_assignee_reply_moves_open_to_answered(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    status = mr.post_message(su, rid, uuid.UUID(STAFF_ID), "looks good, one nit")
    assert status == "answered"


def test_requester_reply_reopens_an_answered_request(su: psycopg.Connection) -> None:
    """Unlimited round-trips: the ball goes back to the assignee."""
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.post_message(su, rid, uuid.UUID(STAFF_ID), "answered")
    status = mr.post_message(su, rid, uuid.UUID(ADMIN_ID), "one more thing")
    assert status == "open"


def test_many_round_trips_are_allowed(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    for _ in range(3):
        mr.post_message(su, rid, uuid.UUID(STAFF_ID), "reply")
        mr.post_message(su, rid, uuid.UUID(ADMIN_ID), "again")
    count = su.execute(
        "select count(*) from app.micro_request_messages where request_id = %s", (rid,)
    ).fetchone()[0]
    assert count == 6


def test_cannot_post_to_a_resolved_request(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.resolve_request(su, rid, uuid.UUID(STAFF_ID))
    with pytest.raises(ValueError, match="resolved"):
        mr.post_message(su, rid, uuid.UUID(STAFF_ID), "too late")


def test_resolving_twice_is_refused(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.resolve_request(su, rid, uuid.UUID(STAFF_ID))
    with pytest.raises(LookupError):
        mr.resolve_request(su, rid, uuid.UUID(STAFF_ID))


# --- turnaround ----------------------------------------------------------------


def test_turnaround_stats_count_resolved_requests(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    mr.resolve_request(su, rid, uuid.UUID(STAFF_ID))
    stats = mr.turnaround_stats(su, uuid.UUID(STAFF_ID))
    assert stats["resolved"] >= 1


def test_a_breached_open_request_shows_in_the_turnaround_view(su: psycopg.Connection) -> None:
    item = _work_item(su)
    past = datetime(2020, 1, 1).astimezone()
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "overdue review",
                            parent_work_item_id=uuid.UUID(item), sla_hours=1, now=past)
    outcome = su.execute(
        "select sla_outcome from app.micro_request_turnaround where id = %s", (rid,)
    ).fetchone()[0]
    assert outcome == "breached"


# --- RLS -----------------------------------------------------------------------


def test_requester_and_assignee_can_see_the_request(su: psycopg.Connection) -> None:
    item = _work_item(su)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(STAFF_ID), "review",
                            parent_work_item_id=uuid.UUID(item))
    with _user_conn(STAFF_ID) as conn:   # the assignee
        n = conn.execute(
            "select count(*) from app.micro_requests where id = %s", (rid,)
        ).fetchone()[0]
        assert n == 1


def test_an_uninvolved_user_cannot_see_a_request_on_a_restricted_matter(
    su: psycopg.Connection,
) -> None:
    """The request hangs off a restricted matter's item; a third party who is neither requester
    nor assignee nor on the matter's ACL sees nothing."""
    # Build a restricted matter + item, request between ADMIN and a fresh third user.
    third = su.execute(
        "insert into app.os_users (id, email, display_name, is_active) "
        "values (gen_random_uuid(), %s, 'Third', true) returning id",
        (f"third-{uuid.uuid4().hex[:6]}@brunetco.com",),
    ).fetchone()[0]
    cid = su.execute("insert into app.clients (code, name) values (%s, 'R Co') returning id",
                     (f"R{uuid.uuid4().hex[:5].upper()}",)).fetchone()[0]
    fid = su.execute(
        "insert into app.families (client_id, family_seq, reference, title, family_type, "
        "restricted) values (%s, '0001', %s, 'S', 'patent', true) returning id",
        (cid, f"S{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    mid = su.execute(
        "insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment, "
        "status) values (%s, %s, 'CA', 'CA', 'pending') returning id",
        (fid, f"M{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    item = _work_item(su, matter_id=mid)
    rid = mr.create_request(su, uuid.UUID(ADMIN_ID), uuid.UUID(str(third)), "review",
                            parent_work_item_id=uuid.UUID(item))
    try:
        with _user_conn(STAFF_ID) as conn:  # neither party, not on the ACL
            n = conn.execute(
                "select count(*) from app.micro_requests where id = %s", (rid,)
            ).fetchone()[0]
            assert n == 0
    finally:
        su.execute("delete from app.micro_requests where id = %s", (rid,))
        su.execute("delete from app.work_items where matter_id = %s", (mid,))
        su.execute("delete from app.matters where id = %s", (mid,))
        su.execute("delete from app.families where id = %s", (fid,))
        su.execute("delete from app.clients where id = %s", (cid,))
        su.execute("delete from app.os_users where id = %s", (third,))
