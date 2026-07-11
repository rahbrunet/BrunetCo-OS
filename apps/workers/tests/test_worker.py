"""Worker consumes a demo event (the API-enqueues -> worker-consumes proof).

Skips cleanly when no DB with migration 0002 is reachable.
"""
from __future__ import annotations

import psycopg
import pytest
from py_shared.config import settings
from worker_app.worker import enqueue, process_once


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('ops.events')").fetchone()
            return row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres with migration 0002 not reachable"
)


def test_enqueue_then_consume() -> None:
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        event_id = enqueue(conn, "demo.ping", {"hello": "world"})
        assert event_id > 0

        handled = process_once(conn)
        assert handled is True

        status = conn.execute(
            "select status from ops.events where id=%s", (event_id,)
        ).fetchone()[0]
        assert status == "done"
