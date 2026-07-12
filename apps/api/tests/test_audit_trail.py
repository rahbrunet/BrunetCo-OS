"""WP 1.5 audit trail (M1-R6) — live-Postgres tests.

Proves: trigger-driven capture on insert/update (with changed_fields), attribution to the
calling user's JWT identity, no-op updates leave no residue, RLS visibility (family-scoped rows
follow can_see_family; family-less rows are admin-only), and immutability (no write surface for
any user, admin included).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"
ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            has = conn.execute("select to_regclass('app.audit_log')").fetchone()[0]
            seeded = conn.execute(
                "select 1 from app.os_users where id = %s", (ADMIN_ID,)
            ).fetchone()
            return has is not None and seeded is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="seeded Postgres (WP1.5) not reachable")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Ctx:
    client_id: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        c.client_id = str(su.execute(
            "insert into app.clients (code, name) values (%s, 'Audit Co') returning id",
            (f"A{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0])
    yield c


def _new_family(token: str, ctx: Ctx, **over: object) -> dict:
    body = {"client_id": ctx.client_id, "title": "Auditable", "family_type": "patent"}
    body.update(over)
    resp = client.post("/api/v1/families", json=body, headers=_hdr(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _audit(token: str, **params: str) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    resp = client.get(f"/api/v1/audit?{query}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Capture + attribution ----------------------------------------------------

def test_family_insert_and_update_audited_with_attribution(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    client.patch(
        f"/api/v1/families/{fam['id']}", json={"title": "Renamed"}, headers=_hdr(STAFF)
    ).raise_for_status()
    entries = _audit(STAFF, table_name="families", row_id=fam["id"])
    actions = [e["action"] for e in entries]
    assert actions == ["update", "insert"]  # newest first
    upd = entries[0]
    assert upd["changed_fields"] == ["title"]
    assert upd["old_row"]["title"] == "Auditable"
    assert upd["new_row"]["title"] == "Renamed"
    assert upd["changed_by"] == STAFF_ID  # attributed to the caller's JWT identity
    assert upd["family_id"] == fam["id"]


def test_task_changes_scoped_to_family(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    matter = client.post(
        "/api/v1/matters",
        json={"family_id": fam["id"], "jurisdiction_code": "CA", "segment_base": "CA"},
        headers=_hdr(STAFF),
    ).json()
    task_id = None
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        task_id = str(su.execute(
            """
            insert into app.tasks (matter_id, title, deadline_type)
            values (%s, 'Audit me', 'internal') returning id
            """,
            (matter["id"],),
        ).fetchone()[0])
    entries = _audit(STAFF, table_name="tasks", row_id=task_id)
    assert len(entries) == 1
    assert entries[0]["action"] == "insert"
    assert entries[0]["family_id"] == fam["id"]      # resolved via the matter
    assert entries[0]["changed_by"] is None          # superuser path = system, not a user


def test_noop_update_leaves_no_audit_residue(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        su.execute(
            "update app.families set title = title where id = %s", (fam["id"],)
        )
    entries = _audit(STAFF, table_name="families", row_id=fam["id"])
    assert [e["action"] for e in entries] == ["insert"]  # no update row


# --- RLS visibility -------------------------------------------------------------

def test_restricted_family_audit_hidden_from_plain_staff(ctx: Ctx) -> None:
    fam = _new_family(ADMIN, ctx, restricted=True)
    staff_view = _audit(STAFF, table_name="families", row_id=fam["id"])
    admin_view = _audit(ADMIN, table_name="families", row_id=fam["id"])
    assert staff_view == []
    assert len(admin_view) == 1


def test_familyless_audit_rows_admin_only(ctx: Ctx) -> None:
    # Grant + revoke a permission → two family-less audit rows on permission_grants.
    client.post(
        "/api/v1/admin/permissions/grants",
        json={"user_id": STAFF_ID, "domain": "invoicing"}, headers=_hdr(ADMIN),
    ).raise_for_status()
    client.delete(
        f"/api/v1/admin/permissions/grants/{STAFF_ID}/invoicing", headers=_hdr(ADMIN)
    ).raise_for_status()
    admin_view = _audit(ADMIN, table_name="permission_grants", row_id=STAFF_ID)
    assert {e["action"] for e in admin_view} >= {"insert", "delete"}
    assert _audit(STAFF, table_name="permission_grants", row_id=STAFF_ID) == []


# --- Immutability ----------------------------------------------------------------

def test_audit_log_has_no_user_write_surface(ctx: Ctx) -> None:
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=ADMIN_ID, email="a@t.local"))
    for stmt in (
        "insert into app.audit_log (table_name, row_id, action)"
        " values ('x', gen_random_uuid(), 'insert')",
        "update app.audit_log set table_name = 'tampered'",
        "delete from app.audit_log",
    ):
        with pytest.raises(psycopg.Error):
            with user_connection(jwt) as conn:
                conn.execute(stmt)
