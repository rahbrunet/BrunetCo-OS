"""API-level smoke: the D44 bridge end-to-end — dev bearer token -> /me and admin endpoints.

Uses the seeded dev users (supabase/seed.sql). Skips without a migrated DB.
"""
from __future__ import annotations

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

PRINCIPAL = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
AGENT = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


def _seeded() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute(
                "select 1 from app.os_users where id = '11111111-1111-1111-1111-111111111111'"
            ).fetchone()
            return row is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _seeded(), reason="seeded Postgres not reachable")

client = TestClient(app)


def test_me_reports_grants() -> None:
    resp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {PRINCIPAL}"})
    assert resp.status_code == 200
    body = resp.json()
    assert "compensation_admin" in body["domains"]


def test_admin_list_scopes_by_caller() -> None:
    principal_view = client.get(
        "/api/v1/admin/permissions", headers={"Authorization": f"Bearer {PRINCIPAL}"}
    ).json()
    agent_view = client.get(
        "/api/v1/admin/permissions", headers={"Authorization": f"Bearer {AGENT}"}
    ).json()
    # Principal sees everyone's domains; the agent sees the roster but only their own grants.
    principal_domains = {u["user_id"]: u["domains"] for u in principal_view}
    agent_domains = {u["user_id"]: u["domains"] for u in agent_view}
    assert principal_domains["22222222-2222-2222-2222-222222222222"] != []
    assert agent_domains["11111111-1111-1111-1111-111111111111"] == []  # RLS hides others' grants


def test_missing_token_is_401() -> None:
    assert client.get("/api/v1/me").status_code == 401
