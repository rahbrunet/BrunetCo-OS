"""WP 6.1 orchestrator — pure gate/broker logic + approval-queue API (RLS-scoped)."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.orchestrator import (
    CredentialDenied,
    EgressRefused,
    broker_authorize,
    egress_check,
)

ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


# --- pure gate + broker ------------------------------------------------------

def test_egress_gate_refuses_unredacted_llm() -> None:
    with pytest.raises(EgressRefused):
        egress_check("llm", None)


def test_egress_gate_allows_redacted_llm_and_non_llm() -> None:
    egress_check("llm", "redaction-audit-123")   # no raise
    egress_check("email", None)                    # non-LLM egress not gated on redaction


def test_broker_denies_unregistered_slot() -> None:
    broker_authorize(["demo/api-key"], "demo/api-key")   # no raise
    with pytest.raises(CredentialDenied):
        broker_authorize(["demo/api-key"], "prod/secret")


# --- approval-queue API ------------------------------------------------------

def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            reg = conn.execute("select to_regclass('app.proposed_actions')").fetchone()[0]
            return reg is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.1 migration (0011) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    matter: str
    restricted_matter: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Orch Co') returning id",
            (f"O{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        fam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (client_id, f"O-{suffix}"),
        ).fetchone()[0]
        c.matter = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA') returning id",
            (fam, f"O-{suffix}-CA"),
        ).fetchone()[0])
        rfam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type,"
            " restricted) values (%s, '0002', %s, 'Secret', 'patent', true) returning id",
            (client_id, f"OR-{suffix}"),
        ).fetchone()[0]
        c.restricted_matter = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA') returning id",
            (rfam, f"OR-{suffix}-CA"),
        ).fetchone()[0])
    yield c


def _propose(token: str, matter_id: str | None, action_type: str = "demo.action") -> object:
    return client.post(
        "/api/v1/approvals",
        json={"agent_name": "demo-agent", "action_type": action_type,
              "payload": {"note": "hi"}, "matter_id": matter_id},
        headers=_hdr(token),
    )


def test_registry_lists_demo_agent(ctx: Ctx) -> None:
    agents = client.get("/api/v1/agents", headers=_hdr(STAFF)).json()
    assert any(a["name"] == "demo-agent" for a in agents)


def test_propose_rejects_unregistered_action(ctx: Ctx) -> None:
    resp = _propose(STAFF, ctx.matter, action_type="filing.stage")  # not in demo-agent's allow-list
    assert resp.status_code == 403


def test_approve_executes_action(ctx: Ctx) -> None:
    pid = _propose(STAFF, ctx.matter).json()["id"]
    resp = client.post(f"/api/v1/approvals/{pid}/approve", headers=_hdr(STAFF))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "executed"
    assert body["outcome"]["handled"] == "demo.action"


def test_reject_does_not_execute(ctx: Ctx) -> None:
    pid = _propose(STAFF, ctx.matter).json()["id"]
    resp = client.post(f"/api/v1/approvals/{pid}/reject", headers=_hdr(STAFF))
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["outcome"] is None


def test_cannot_decide_twice(ctx: Ctx) -> None:
    pid = _propose(STAFF, ctx.matter).json()["id"]
    assert client.post(f"/api/v1/approvals/{pid}/approve", headers=_hdr(STAFF)).status_code == 200
    again = client.post(f"/api/v1/approvals/{pid}/approve", headers=_hdr(STAFF))
    assert again.status_code == 409


def test_queue_is_rls_scoped(ctx: Ctx) -> None:
    # A proposal on a restricted matter is invisible to plain staff, visible to the admin.
    pid = _propose(ADMIN, ctx.restricted_matter).json()["id"]
    staff_queue = client.get(
        f"/api/v1/approvals?matter_id={ctx.restricted_matter}", headers=_hdr(STAFF)
    ).json()
    assert staff_queue == []
    admin_queue = client.get(
        f"/api/v1/approvals?matter_id={ctx.restricted_matter}", headers=_hdr(ADMIN)
    ).json()
    assert any(p["id"] == pid for p in admin_queue)
    # And staff cannot approve what they cannot see.
    assert client.post(f"/api/v1/approvals/{pid}/approve", headers=_hdr(STAFF)).status_code == 404
