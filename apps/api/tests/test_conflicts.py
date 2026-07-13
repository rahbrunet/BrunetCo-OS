"""WP 5B.4 conflict checks (D34/D38) — firm-wide search (incl. restricted) + logged results."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.domain.conflicts import normalize_query

STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


# --- pure helper -------------------------------------------------------------

def test_normalize_query() -> None:
    assert normalize_query("  Acme   Corp ") == "Acme Corp"
    with pytest.raises(ValueError):
        normalize_query("   ")


# --- API ---------------------------------------------------------------------

def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            reg = conn.execute("select to_regclass('app.conflict_checks')").fetchone()[0]
            return reg is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5B.4 migration (0012) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    unique_name: str
    restricted_name: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    tag = uuid.uuid4().hex[:8]
    c.unique_name = f"Zephyron Dynamics {tag}"
    c.restricted_name = f"Umbra Secretive Holdings {tag}"
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        # A normal client (visible to staff).
        su.execute(
            "insert into app.clients (code, name) values (%s, %s)",
            (f"Z{tag[:4].upper()}", c.unique_name),
        )
        # A client whose only matter is in a RESTRICTED family — staff cannot see it directly,
        # but the conflict search (definer) must still surface it.
        rclient = su.execute(
            "insert into app.clients (code, name) values (%s, %s) returning id",
            (f"U{tag[:4].upper()}", c.restricted_name),
        ).fetchone()[0]
        rfam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type,"
            " restricted) values (%s, '0001', %s, %s, 'patent', true) returning id",
            (rclient, f"U-{tag}", f"Umbra Widget {tag}"),
        ).fetchone()[0]
        su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA')",
            (rfam, f"U-{tag}-CA"),
        )
    yield c


def _check(query: str, **kw) -> dict:
    body = {"query": query, **kw}
    return client.post("/api/v1/conflicts/check", json=body, headers=_hdr(STAFF)).json()


def test_finds_existing_client(ctx: Ctx) -> None:
    res = _check(ctx.unique_name)
    assert res["result_count"] >= 1
    assert any(m["label"] == ctx.unique_name and m["kind"] == "client" for m in res["matches"])


def test_fuzzy_match_on_typo(ctx: Ctx) -> None:
    # Drop a letter — trigram similarity should still surface it.
    typo = ctx.unique_name.replace("Zephyron", "Zephyon")
    res = _check(typo)
    assert any("Zephyron Dynamics" in m["label"] for m in res["matches"])


def test_clean_query_returns_no_matches(ctx: Ctx) -> None:
    res = _check(f"Nonexistent Party {uuid.uuid4().hex}")
    assert res["result_count"] == 0
    assert res["matches"] == []


def test_search_sees_restricted_family_party(ctx: Ctx) -> None:
    # The restricted client's matter is invisible to staff via normal RLS, but a conflict check
    # MUST surface the party (D38 — the conflict you can't see is the one that matters).
    res = _check(ctx.restricted_name)
    assert any(m["label"] == ctx.restricted_name for m in res["matches"])


def test_check_is_logged_and_clearable(ctx: Ctx) -> None:
    res = _check(ctx.unique_name, check_type="intake")
    cid = res["check_id"]
    log = client.get("/api/v1/conflicts", headers=_hdr(STAFF)).json()
    entry = next(e for e in log if e["id"] == cid)
    assert entry["check_type"] == "intake"
    assert entry["result_count"] == res["result_count"]
    assert entry["cleared"] is False
    cleared = client.post(f"/api/v1/conflicts/{cid}/clear", headers=_hdr(STAFF))
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True


def test_empty_query_is_422(ctx: Ctx) -> None:
    resp = client.post("/api/v1/conflicts/check", json={"query": "   "}, headers=_hdr(STAFF))
    assert resp.status_code == 422
