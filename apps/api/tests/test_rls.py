"""D44 RLS proof — connects DIRECTLY to Postgres with two user JWTs and proves row isolation.

This is the pattern the WP 9.1 permission acceptance tests reuse. It deliberately does NOT go
through the API: it proves the database itself (RLS) is the control, so app-code bugs cannot
grant cross-user access.

Requires a running Postgres with migration 0001 applied (CI provides this; locally run
`npx supabase start && python make.py migrate`, or point SUPABASE_DB_URL at any Postgres and
apply supabase/migrations/0001_demo_rls.sql). Skips cleanly if no DB is reachable.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection
from py_shared.config import settings


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("select to_regclass('app.demo_notes')")
                return cur.fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres with migration 0001 not reachable (run supabase start + migrate)",
)


def _jwt_for() -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    identity = EntraIdentity(os_user_id=user_id, email=f"{user_id[:8]}@dev.local")
    return user_id, mint_supabase_jwt(identity)


def test_users_see_only_their_own_rows() -> None:
    user_a, jwt_a = _jwt_for()
    user_b, jwt_b = _jwt_for()

    # Each user creates a row under their own identity (insert policy: owner_id = jwt_uid()).
    with user_connection(jwt_a) as conn:
        conn.execute("insert into app.demo_notes (body) values (%s)", ("a-secret",))
    with user_connection(jwt_b) as conn:
        conn.execute("insert into app.demo_notes (body) values (%s)", ("b-secret",))

    # User A sees only A's row; B's row is invisible at the database layer.
    with user_connection(jwt_a) as conn:
        rows_a = conn.execute("select body from app.demo_notes").fetchall()
    with user_connection(jwt_b) as conn:
        rows_b = conn.execute("select body from app.demo_notes").fetchall()

    bodies_a = {r[0] for r in rows_a}
    bodies_b = {r[0] for r in rows_b}

    assert "a-secret" in bodies_a and "b-secret" not in bodies_a
    assert "b-secret" in bodies_b and "a-secret" not in bodies_b
