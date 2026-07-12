"""WP 1.4 daily docket digest — worker tests against live Postgres.

Proves: per-user digest composition (overdue vs upcoming split, horizon cut-off), the
docket.daily_digest → email.send fan-out through the real event queue, and the transport stub.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import psycopg
import pytest
from py_shared.config import settings
from worker_app.digest import build_daily_digests, handle_daily_digest
from worker_app.worker import enqueue, process_once

TODAY = date.today()


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.tasks')").fetchone()
            return row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="Postgres (WP1.4) not reachable")


class Ctx:
    user_id: str
    email: str
    matter_ref: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    c.user_id = str(uuid.uuid4())
    c.email = f"digest-{c.user_id[:8]}@t.local"
    suffix = uuid.uuid4().hex[:6]
    c.matter_ref = f"G-{suffix}-CA"
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        su.execute(
            "insert into app.os_users (id, email, display_name) values (%s, %s, 'Digest User')",
            (c.user_id, c.email),
        )
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Digest Co') returning id",
            (f"G{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        family_id = su.execute(
            """
            insert into app.families (client_id, family_seq, reference, title, family_type)
            values (%s, '0001', %s, 'Digestible', 'patent') returning id
            """,
            (client_id, f"G-{suffix}"),
        ).fetchone()[0]
        matter_id = su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (family_id, c.matter_ref),
        ).fetchone()[0]
        for title, respond_by in (
            ("Overdue thing", TODAY - timedelta(days=2)),
            ("Due tomorrow", TODAY + timedelta(days=1)),
            ("Beyond horizon", TODAY + timedelta(days=30)),
        ):
            su.execute(
                """
                insert into app.tasks
                  (matter_id, title, deadline_type, respond_by, assignee_id)
                values (%s, %s, 'hard_external', %s, %s)
                """,
                (matter_id, title, respond_by, c.user_id),
            )
    yield c


def test_digest_splits_overdue_and_upcoming_within_horizon(ctx: Ctx) -> None:
    with psycopg.connect(settings.supabase_db_url) as conn:
        digests = build_daily_digests(conn, TODAY, horizon_days=7)
    mine = next(d for d in digests if d.user_id == ctx.user_id)
    assert mine.task_count == 2  # horizon excludes the +30d task
    assert "OVERDUE (1):" in mine.body
    assert "Overdue thing" in mine.body
    assert "Due tomorrow" in mine.body
    assert "Beyond horizon" not in mine.body
    assert ctx.matter_ref in mine.body
    assert mine.email == ctx.email
    assert "1 overdue, 1 upcoming" in mine.subject


def test_daily_digest_event_fans_out_email_send(ctx: Ctx) -> None:
    handle_daily_digest({"as_of": TODAY.isoformat()})
    with psycopg.connect(settings.supabase_db_url) as conn:
        pending = conn.execute(
            """
            select payload from ops.events
             where type = 'email.send' and status = 'pending'
               and payload ->> 'to' = %s
            """,
            (ctx.email,),
        ).fetchall()
    assert len(pending) >= 1
    assert "Docket" in pending[0][0]["subject"]


def test_email_send_stub_consumes_event(ctx: Ctx) -> None:
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        event_id = enqueue(conn, "email.send", {"to": ctx.email, "subject": "t", "body": "b"})
        # Drain until our event is handled (other pending events may precede it).
        for _ in range(200):
            status = conn.execute(
                "select status from ops.events where id=%s", (event_id,)
            ).fetchone()[0]
            if status != "pending":
                break
            assert process_once(conn)
        status = conn.execute(
            "select status from ops.events where id=%s", (event_id,)
        ).fetchone()[0]
    assert status == "done"
