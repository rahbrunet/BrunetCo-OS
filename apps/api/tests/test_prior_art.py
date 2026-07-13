"""WP 4A.1 prior-art references + §1.56 citation states — dedup, cross-linking, RLS."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.domain.prior_art import normalize_citation

STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"
ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"


# --- pure normalization ------------------------------------------------------

def test_normalize_patent_citation() -> None:
    assert normalize_citation("us 1,234,567 b2") == "US1234567B2"
    assert normalize_citation("US-1234567-B2") == "US1234567B2"


def test_normalize_npl_keeps_text() -> None:
    out = normalize_citation("Smith et al.,  Nature 2020", kind="npl")
    assert out == "Smith et al., Nature 2020"


# --- API ---------------------------------------------------------------------

def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            reg = conn.execute("select to_regclass('app.reference_links')").fetchone()[0]
            return reg is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP4A.1 migration (0013) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    matter_a: str
    matter_b: str            # sibling in the same family
    restricted_matter: str
    tag: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    c.tag = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        cid = su.execute(
            "insert into app.clients (code, name) values (%s, 'PA Co') returning id",
            (f"P{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        fam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (cid, f"P-{c.tag}"),
        ).fetchone()[0]
        c.matter_a = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'US', 'US') returning id",
            (fam, f"P-{c.tag}-US"),
        ).fetchone()[0])
        c.matter_b = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA') returning id",
            (fam, f"P-{c.tag}-CA"),
        ).fetchone()[0])
        rfam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type,"
            " restricted) values (%s, '0002', %s, 'Secret', 'patent', true) returning id",
            (cid, f"PR-{c.tag}"),
        ).fetchone()[0]
        c.restricted_matter = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'US', 'US') returning id",
            (rfam, f"PR-{c.tag}-US"),
        ).fetchone()[0])
    yield c


def _add_ref(citation: str, **kw) -> dict:
    return client.post(
        "/api/v1/prior-art/references", json={"citation": citation, **kw}, headers=_hdr(STAFF)
    ).json()


def test_reference_dedup_on_normalized_citation(ctx: Ctx) -> None:
    cit = f"US{ctx.tag}123B2"
    first = _add_ref(cit, title="Gizmo")
    assert first["created"] is True
    # Same document, messy spelling → same row, not created.
    second = _add_ref(f"us {ctx.tag} 123 b2")
    assert second["created"] is False
    assert second["id"] == first["id"]


def test_reference_cross_links_to_sibling_matters(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}777B2")["id"]
    for m in (ctx.matter_a, ctx.matter_b):
        resp = client.post(
            "/api/v1/prior-art/links",
            json={"reference_id": ref, "matter_id": m, "citation_state": "to_disclose",
                  "ids_bundle": "PriorArt-IDS1"},
            headers=_hdr(STAFF),
        )
        assert resp.status_code == 201, resp.text
    # The same reference now appears in both sibling matters (family-wide art).
    cites = client.get(
        f"/api/v1/prior-art/citations?family_id={_family_of(ctx.matter_a)}", headers=_hdr(STAFF)
    ).json()
    refs_for = [c for c in cites if c["reference_id"] == ref]
    assert {c["matter_id"] for c in refs_for} == {ctx.matter_a, ctx.matter_b}


def _family_of(matter_id: str) -> str:
    with psycopg.connect(settings.supabase_db_url) as conn:
        return str(conn.execute(
            "select family_id from app.matters where id = %s", (matter_id,)
        ).fetchone()[0])


def test_citation_state_advances(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}888B2")["id"]
    link = client.post(
        "/api/v1/prior-art/links",
        json={"reference_id": ref, "matter_id": ctx.matter_a}, headers=_hdr(STAFF),
    ).json()["id"]
    resp = client.patch(
        f"/api/v1/prior-art/links/{link}", json={"citation_state": "disclosed"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 200
    cites = client.get(
        f"/api/v1/prior-art/citations?matter_id={ctx.matter_a}", headers=_hdr(STAFF)
    ).json()
    assert any(c["link_id"] == link and c["citation_state"] == "disclosed" for c in cites)


def test_relink_updates_state_not_duplicates(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}999B2")["id"]
    body = {"reference_id": ref, "matter_id": ctx.matter_b}
    first = client.post("/api/v1/prior-art/links", json=body, headers=_hdr(STAFF)).json()["id"]
    second = client.post(
        "/api/v1/prior-art/links",
        json={**body, "citation_state": "considered"}, headers=_hdr(STAFF),
    ).json()["id"]
    assert first == second   # same (reference, matter) link, upserted


def test_link_on_restricted_matter_hidden_from_staff(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}555B2")["id"]
    # Admin links it on the restricted matter.
    admin_link = client.post(
        "/api/v1/prior-art/links",
        json={"reference_id": ref, "matter_id": ctx.restricted_matter}, headers=_hdr(ADMIN),
    )
    assert admin_link.status_code == 201
    # Staff cannot see that citation (link follows matter visibility).
    staff_view = client.get(
        f"/api/v1/prior-art/citations?matter_id={ctx.restricted_matter}", headers=_hdr(STAFF)
    ).json()
    assert staff_view == []
    # And staff cannot create a link there either.
    denied = client.post(
        "/api/v1/prior-art/links",
        json={"reference_id": ref, "matter_id": ctx.restricted_matter}, headers=_hdr(STAFF),
    )
    assert denied.status_code == 404
