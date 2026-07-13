"""WP 4A.2 cross-citation matrix + bulk cross-cite + duty-of-disclosure dashboard."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.domain.prior_art import build_matrix

STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


# --- pure matrix assembly ----------------------------------------------------

def test_build_matrix_grid() -> None:
    rows = [
        ("r1", "US111B2", "Alpha", "m1", "X-CA", "to_disclose"),
        ("r1", "US111B2", "Alpha", "m2", "X-US", "disclosed"),
        ("r2", "US222B2", "Beta", "m1", "X-CA", "considered"),
    ]
    grid = build_matrix(rows)
    assert [r["citation"] for r in grid["references"]] == ["US111B2", "US222B2"]
    assert [m["reference"] for m in grid["matters"]] == ["X-CA", "X-US"]
    assert grid["cells"]["r1"] == {"m1": "to_disclose", "m2": "disclosed"}
    assert grid["cells"]["r2"] == {"m1": "considered"}
    assert "m2" not in grid["cells"]["r2"]   # r2 not cited in m2 → absent


# --- API ---------------------------------------------------------------------

def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            reg = conn.execute("select to_regclass('app.reference_links')").fetchone()[0]
            return reg is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP4A.1/4A.2 migration (0013) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    family: str
    matter_a: str
    matter_b: str
    tag: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    c.tag = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        cid = su.execute(
            "insert into app.clients (code, name) values (%s, 'Matrix Co') returning id",
            (f"X{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        c.family = str(su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (cid, f"X-{c.tag}"),
        ).fetchone()[0])
        c.matter_a = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'US', 'US') returning id",
            (c.family, f"X-{c.tag}-US"),
        ).fetchone()[0])
        c.matter_b = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA') returning id",
            (c.family, f"X-{c.tag}-CA"),
        ).fetchone()[0])
    yield c


def _add_ref(citation: str) -> str:
    return client.post(
        "/api/v1/prior-art/references", json={"citation": citation}, headers=_hdr(STAFF)
    ).json()["id"]


def test_bulk_link_hits_every_matter_in_family(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}BULK")
    resp = client.post(
        "/api/v1/prior-art/bulk-link",
        json={"reference_id": ref, "family_id": ctx.family, "ids_bundle": "PriorArt-IDS2"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["linked"] == 2       # both matters in the family
    grid = client.get(
        f"/api/v1/prior-art/matrix?family_id={ctx.family}", headers=_hdr(STAFF)
    ).json()
    assert grid["cells"][ref] == {ctx.matter_a: "to_disclose", ctx.matter_b: "to_disclose"}


def test_duty_dashboard_counts_and_outstanding(ctx: Ctx) -> None:
    ref = _add_ref(f"US{ctx.tag}DUTY")
    # Link to both matters, then mark one disclosed.
    client.post(
        "/api/v1/prior-art/bulk-link",
        json={"reference_id": ref, "family_id": ctx.family}, headers=_hdr(STAFF),
    )
    cites = client.get(
        f"/api/v1/prior-art/citations?matter_id={ctx.matter_a}", headers=_hdr(STAFF)
    ).json()
    link_a = next(c["link_id"] for c in cites if c["reference_id"] == ref)
    client.patch(
        f"/api/v1/prior-art/links/{link_a}", json={"citation_state": "disclosed"},
        headers=_hdr(STAFF),
    )
    duty = client.get(
        f"/api/v1/prior-art/duty?family_id={ctx.family}", headers=_hdr(STAFF)
    ).json()
    assert duty["counts_by_state"].get("disclosed", 0) >= 1
    assert duty["counts_by_state"].get("to_disclose", 0) >= 1
    # The outstanding list carries the still-to-disclose citation on matter_b.
    outstanding_refs = {(o["matter_id"]) for o in duty["outstanding"]}
    assert ctx.matter_b in outstanding_refs
