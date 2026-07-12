"""WP 1.1 Family/Matter CRUD + reference generation — API tests against live Postgres.

Same live-Postgres pattern as test_domain_rls.py: data is staged via the superuser connection,
then every assertion runs through the API under a dev bearer token so RLS (not app code) is the
control being proved. Covers:
  * reference-generation ordering (US, US2, US3) and USP independence,
  * PCT / MP as sibling matters (not nested children),
  * the families uniqueness constraint (409),
  * RLS-scoped visibility of the new endpoints (restricted family: admin/ACL yes, plain staff no).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

# Seeded dev identities (supabase/seed.sql): Principal holds compensation_admin → permissions
# admin; Agent is plain active staff with no family ACLs.
ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            has_matters = conn.execute("select to_regclass('app.matters')").fetchone()[0]
            seeded = conn.execute(
                "select 1 from app.os_users where id = '11111111-1111-1111-1111-111111111111'"
            ).fetchone()
            return has_matters is not None and seeded is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="seeded Postgres (WP1.1) not reachable")

client = TestClient(app)


class Ctx:
    client_id: str
    restricted_family: str
    restricted_matter: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        c.client_id = str(su.execute(
            "insert into app.clients (code, name) values (%s, 'CRUD Co') returning id",
            (f"C{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0])
        # Restricted family + matter, ACL-granted to Principal only (as its granter), so plain
        # staff (Agent) cannot see it but the permissions admin can.
        c.restricted_family = str(su.execute(
            """
            insert into app.families
              (client_id, family_seq, reference, title, family_type, restricted)
            values (%s, '0500', %s, 'Secret', 'patent', true) returning id
            """,
            (c.client_id, f"R-{uuid.uuid4().hex[:8]}"),
        ).fetchone()[0])
        c.restricted_matter = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (c.restricted_family, f"R-{uuid.uuid4().hex[:8]}-CA"),
        ).fetchone()[0])
    yield c


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _new_family(token: str, ctx: Ctx, **over: object) -> dict:
    body = {"client_id": ctx.client_id, "title": "Widget", "family_type": "patent"}
    body.update(over)
    resp = client.post("/api/v1/families", json=body, headers=_hdr(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _new_matter(token: str, family_id: str, base: str, **over: object) -> dict:
    body: dict[str, object] = {
        "family_id": family_id, "jurisdiction_code": base or "ZZ", "segment_base": base,
    }
    body.update(over)
    resp = client.post("/api/v1/matters", json=body, headers=_hdr(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Family CRUD + tag ------------------------------------------------------

def test_family_create_autoallocates_seq_and_reference(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    assert fam["family_seq"].isdigit() and len(fam["family_seq"]) == 4
    assert fam["reference"].endswith(f"-{fam['family_seq']}")
    assert fam["display_reference"] == fam["reference"]  # patent → untagged


def test_trademark_family_display_tag(ctx: Ctx) -> None:
    word = _new_family(STAFF, ctx, family_type="trademark", tm_design=False)
    design = _new_family(STAFF, ctx, family_type="trademark", tm_design=True)
    assert word["display_reference"] == f"{word['reference']} (TM)"
    assert design["display_reference"] == f"{design['reference']} Design"


def test_family_get_and_patch(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    got = client.get(f"/api/v1/families/{fam['id']}", headers=_hdr(STAFF))
    assert got.status_code == 200 and got.json()["reference"] == fam["reference"]
    patched = client.patch(
        f"/api/v1/families/{fam['id']}", json={"title": "Renamed"}, headers=_hdr(STAFF)
    )
    assert patched.status_code == 200 and patched.json()["title"] == "Renamed"


def test_duplicate_family_seq_conflicts(ctx: Ctx) -> None:
    _new_family(STAFF, ctx, family_seq="0900")
    dup = client.post(
        "/api/v1/families",
        json={"client_id": ctx.client_id, "title": "Clash", "family_type": "patent",
              "family_seq": "0900"},
        headers=_hdr(STAFF),
    )
    assert dup.status_code == 409, dup.text


# --- Matter reference ordering ----------------------------------------------

def test_segment_ordering_us_us2_us3_with_usp_independent(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    assert _new_matter(STAFF, fam["id"], "US")["jurisdiction_segment"] == "US"
    assert _new_matter(STAFF, fam["id"], "US")["jurisdiction_segment"] == "US2"
    assert _new_matter(STAFF, fam["id"], "US")["jurisdiction_segment"] == "US3"
    # A provisional gets its own track and does not perturb the regular US sequence.
    assert _new_matter(STAFF, fam["id"], "USP")["jurisdiction_segment"] == "USP"
    us4 = _new_matter(STAFF, fam["id"], "US")
    assert us4["jurisdiction_segment"] == "US4"
    assert us4["reference"].endswith("-US4")


def test_pct_and_mp_are_siblings_not_children(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    pct = _new_matter(STAFF, fam["id"], "PCT")
    mp = _new_matter(STAFF, fam["id"], "MP")
    # National-phase / Madrid designations link via parent_matter_id + relationship_type only;
    # their reference segment is the ordinary country code at the SAME level as the vehicle.
    national = _new_matter(
        STAFF, fam["id"], "US",
        parent_matter_id=pct["id"], relationship_type="pct_national_phase",
    )
    madrid = _new_matter(
        STAFF, fam["id"], "CA",
        parent_matter_id=mp["id"], relationship_type="madrid_designation",
    )
    assert pct["jurisdiction_segment"] == "PCT" and pct["reference"].endswith("-PCT")
    assert mp["jurisdiction_segment"] == "MP"
    # Child segment is a family-level country code, NOT nested under the vehicle.
    assert national["jurisdiction_segment"] == "US"
    assert national["reference"].endswith("-US")
    assert "PCT" not in national["reference"].rsplit("-", 1)[1]
    assert national["parent_matter_id"] == pct["id"]
    assert national["relationship_type"] == "pct_national_phase"
    assert madrid["parent_matter_id"] == mp["id"]


def test_parent_without_relationship_type_is_422(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    parent = _new_matter(STAFF, fam["id"], "US")
    resp = client.post(
        "/api/v1/matters",
        json={"family_id": fam["id"], "jurisdiction_code": "US", "segment_base": "US",
              "parent_matter_id": parent["id"]},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 422, resp.text


def test_matter_patch_updates_status(ctx: Ctx) -> None:
    fam = _new_family(STAFF, ctx)
    matter = _new_matter(STAFF, fam["id"], "US")
    patched = client.patch(
        f"/api/v1/matters/{matter['id']}",
        json={"status": "filed", "application_no": "17/123,456"}, headers=_hdr(STAFF),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "filed"
    assert patched.json()["application_no"] == "17/123,456"


# --- RLS-scoped visibility on the new endpoints -----------------------------

def test_restricted_family_hidden_from_plain_staff(ctx: Ctx) -> None:
    # Agent is active staff but has no ACL on the restricted family → 404 at the DB layer.
    assert client.get(
        f"/api/v1/families/{ctx.restricted_family}", headers=_hdr(STAFF)
    ).status_code == 404
    assert client.get(
        f"/api/v1/matters/{ctx.restricted_matter}", headers=_hdr(STAFF)
    ).status_code == 404


def test_restricted_family_visible_to_permissions_admin(ctx: Ctx) -> None:
    assert client.get(
        f"/api/v1/families/{ctx.restricted_family}", headers=_hdr(ADMIN)
    ).status_code == 200
    assert client.get(
        f"/api/v1/matters/{ctx.restricted_matter}", headers=_hdr(ADMIN)
    ).status_code == 200


def test_plain_staff_cannot_create_matter_in_restricted_family(ctx: Ctx) -> None:
    # RLS with-check on insert denies a family the caller cannot see → mapped to 403.
    resp = client.post(
        "/api/v1/matters",
        json={"family_id": ctx.restricted_family, "jurisdiction_code": "JP",
              "segment_base": "JP"},
        headers=_hdr(STAFF),
    )
    # The family read inside the generator returns no row → 400 "not visible"; either way it is
    # blocked (never 201). Assert it is denied.
    assert resp.status_code in (400, 403, 404), resp.text
