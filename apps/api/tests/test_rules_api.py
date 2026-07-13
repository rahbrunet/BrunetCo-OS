"""WP 1.3 rule management + import API — against live Postgres (test_docketing_engine pattern).

Covers: admin-gated import with correct bucketing + idempotency, imported rules land draft/inactive
(engine never fires them), unresolved queue, dual-mode summary, the dry-run simulator (no persist),
the approval gate (approve → engine fires), versioned edit, and RLS (staff read-only).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"
ADMIN_ID = "11111111-1111-1111-1111-111111111111"
FIXTURE = Path(__file__).parent / "fixtures" / "appcoll_task_types_sample.csv"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            return conn.execute(
                "select to_regclass('app.rule_import_unresolved')"
            ).fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP1.3 migration (0009) not applied")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _files() -> dict:
    return {"file": ("task_types.csv", FIXTURE.read_bytes(), "text/csv")}


class Ctx:
    matter_ca: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Rules Co') returning id",
            (f"R{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        family_id = su.execute(
            """
            insert into app.families (client_id, family_seq, reference, title, family_type)
            values (%s, '0001', %s, 'Widget', 'patent') returning id
            """,
            (client_id, f"R-{suffix}"),
        ).fetchone()[0]
        c.matter_ca = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (family_id, f"R-{suffix}-CA"),
        ).fetchone()[0])
    yield c


def _import(token: str):
    return client.post("/api/v1/rules/import", files=_files(), headers=_hdr(token))


# --- import: admin-gated, correct bucketing ----------------------------------

def test_staff_cannot_import(ctx: Ctx) -> None:
    assert _import(STAFF).status_code == 403


def test_admin_import_buckets_and_counts(ctx: Ctx) -> None:
    resp = _import(ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_rows"] == 13
    assert body["imported"] + body["updated"] == 9
    assert body["unresolved"] == 3
    assert body["ladder_stubs"] == 1
    assert body["superseded_by_a1"] == 1
    assert body["deadline_type_counts"] == {
        "extendable_external": 2, "hard_external": 2, "internal": 1,
        "general_reminder": 1, "event": 2, "transient_event": 1,
    }


def test_reimport_is_idempotent(ctx: Ctx) -> None:
    # Second import updates the same 9 rules, creates zero new ones (idempotent on appcoll id).
    _import(ADMIN)
    second = _import(ADMIN).json()
    assert second["imported"] == 0
    assert second["updated"] == 9
    # The unresolved queue is append-only per run by design, but rule count stays flat.
    with psycopg.connect(settings.supabase_db_url) as conn:
        n = conn.execute(
            "select count(*) from app.docket_rules where source = 'appcoll_import'"
        ).fetchone()[0]
    assert n == 9


# --- imported rules are draft/inactive ---------------------------------------

def _find_rule(token: str, appcoll_id: str) -> dict:
    rules = client.get("/api/v1/rules", headers=_hdr(token)).json()
    return next(r for r in rules if r["appcoll_task_type_id"] == appcoll_id)


def test_imported_rules_land_draft_inactive(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1001")
    assert r["active"] is False
    assert r["approval_status"] == "draft"
    assert "office action occurs" in r["summary"]


def test_draft_rule_does_not_fire(ctx: Ctx) -> None:
    _import(ADMIN)
    # TT-1001 fires on 'office_action' (CA). While draft/inactive, the engine generates nothing.
    resp = client.post(
        "/api/v1/docket/trigger",
        json={"matter_id": ctx.matter_ca, "trigger_code": "office_action",
              "ref_date": "2026-03-10"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 201
    assert resp.json() == []


# --- unresolved queue --------------------------------------------------------

def test_unresolved_queue_populated(ctx: Ctx) -> None:
    _import(ADMIN)
    q = client.get("/api/v1/rules/unresolved", headers=_hdr(ADMIN)).json()
    ids = {row["appcoll_task_type_id"] for row in q}
    assert {"TT-1011", "TT-1012", "TT-1013"} <= ids


# --- simulator (dry-run, no persist) -----------------------------------------

def test_simulate_previews_without_persisting(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1001")
    with psycopg.connect(settings.supabase_db_url) as conn:
        before = conn.execute(
            "select count(*) from app.task_provenance where matter_id = %s", (ctx.matter_ca,)
        ).fetchone()[0]
    resp = client.post(
        f"/api/v1/rules/{r['rule_id']}/simulate",
        json={"matter_id": ctx.matter_ca, "ref_date": "2026-03-10"},
        headers=_hdr(STAFF),  # staff CAN preview
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["respond_by"] == "2026-07-10"   # +4m from 2026-03-10
    assert body["final_due_date"] == "2026-09-10"  # +6m
    assert "summary" in body
    with psycopg.connect(settings.supabase_db_url) as conn:
        after = conn.execute(
            "select count(*) from app.task_provenance where matter_id = %s", (ctx.matter_ca,)
        ).fetchone()[0]
    assert after == before  # nothing persisted


# --- approval gate → engine fires --------------------------------------------

def test_approve_activates_and_engine_fires(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1002")  # Hard External, CA, trigger 'pct_filing', +30m
    approved = client.post(f"/api/v1/rules/{r['rule_id']}/approve", headers=_hdr(ADMIN))
    assert approved.status_code == 200, approved.text
    assert approved.json()["active"] is True
    assert approved.json()["approval_status"] == "approved"
    # Now the engine fires it.
    resp = client.post(
        "/api/v1/docket/trigger",
        json={"matter_id": ctx.matter_ca, "trigger_code": "pct_filing", "ref_date": "2024-01-15"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 201
    tasks = resp.json()
    assert len(tasks) == 1
    assert tasks[0]["final_due_date"] == "2026-07-15"  # +30 months


def test_staff_cannot_approve(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1003")
    resp = client.post(f"/api/v1/rules/{r['rule_id']}/approve", headers=_hdr(STAFF))
    assert resp.status_code == 403


# --- versioned edit ----------------------------------------------------------

def test_edit_creates_new_draft_version(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1004")
    new_def = dict(r["definition"])
    new_def["offsets"] = {"final_due_date": {"days": 21}}
    resp = client.post(
        f"/api/v1/rules/{r['rule_id']}/versions",
        json={"definition": new_def}, headers=_hdr(ADMIN),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == r["version"] + 1
    assert resp.json()["active"] is False
    assert resp.json()["approval_status"] == "draft"


def test_edit_rejects_invalid_definition(ctx: Ctx) -> None:
    _import(ADMIN)
    r = _find_rule(ADMIN, "TT-1005")
    resp = client.post(
        f"/api/v1/rules/{r['rule_id']}/versions",
        json={"definition": {"title": "x"}},  # no deadline_type / offsets
        headers=_hdr(ADMIN),
    )
    assert resp.status_code == 422
