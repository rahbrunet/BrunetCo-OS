"""Event worker — claims events with FOR UPDATE SKIP LOCKED and dispatches by type.

WP 0.7 ships one handler (`demo.ping`) that logs and marks the event done, proving the
API-enqueues -> worker-consumes loop. Real handlers (docketing, billing webhooks, email
ingestion) register here at WPs 1.x / 3.3 / 4.x.

Workers use a system identity (the connection here may use the service-role DB path per D44's
enumerated exceptions), never a user JWT.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import psycopg
from py_shared.config import settings

Handler = Callable[[dict[str, Any]], None]


def _handle_demo_ping(payload: dict[str, Any]) -> None:
    print(f"[worker] demo.ping received: {json.dumps(payload)}")


HANDLERS: dict[str, Handler] = {
    "demo.ping": _handle_demo_ping,
}


def enqueue(conn: psycopg.Connection, event_type: str, payload: dict[str, Any]) -> int:
    row = conn.execute(
        "insert into ops.events (type, payload) values (%s, %s) returning id",
        (event_type, json.dumps(payload)),
    ).fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("insert returned no id")
    return int(row[0])


def process_once(conn: psycopg.Connection, worker_id: str = "worker-0") -> bool:
    """Claim and process a single event. Returns True if one was handled."""
    row = conn.execute("select * from ops.claim_next_event(%s)", (worker_id,)).fetchone()
    conn.commit()
    if row is None or row[0] is None:
        return False

    # ops.claim_next_event returns the composite row: (id, type, payload, status, ...)
    event_id, event_type, payload = row[0], row[1], row[2]
    handler = HANDLERS.get(event_type)
    try:
        if handler is None:
            raise ValueError(f"no handler for event type {event_type!r}")
        handler(payload if isinstance(payload, dict) else json.loads(payload))
        conn.execute(
            "update ops.events set status='done', processed_at=now() where id=%s", (event_id,)
        )
    except Exception as exc:  # noqa: BLE001 — record failure, keep the loop alive
        print(f"[worker] event {event_id} failed: {exc}")
        conn.execute("update ops.events set status='failed' where id=%s", (event_id,))
    conn.commit()
    return True


def run_forever(poll_seconds: float = 1.0) -> None:  # pragma: no cover - long-running
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        print("[worker] started")
        while True:
            if not process_once(conn):
                time.sleep(poll_seconds)


if __name__ == "__main__":  # pragma: no cover
    run_forever()
