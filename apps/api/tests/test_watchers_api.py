"""WP 6.2 watcher observability API — failure tags queryable per matter (RLS-scoped) + run log."""
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


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.watcher_failures')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.2 migration (0014) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    matter: str
    restricted_matter: str
    run_id: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Watcher Co') returning id",
            (f"V{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        fam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (client_id, f"V-{suffix}"),
        ).fetchone()[0]
        fam_r = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type, "
            "restricted) values (%s, '0002', %s, 'Secret', 'patent', true) returning id",
            (client_id, f"VR-{suffix}"),
        ).fetchone()[0]
        c.matter = str(su.execute(
            "insert into app.matters (family_id, reference, jurisdiction_code, "
            "jurisdiction_segment, application_no) values (%s, %s, 'CA', 'CA', %s) returning id",
            (fam, f"V-{suffix}-CA", f"1{uuid.uuid4().int % 10**6:06d}"),
        ).fetchone()[0])
        c.restricted_matter = str(su.execute(
            "insert into app.matters (family_id, reference, jurisdiction_code, "
            "jurisdiction_segment, application_no) values (%s, %s, 'CA', 'CA', %s) returning id",
            (fam_r, f"VR-{suffix}-CA", f"2{uuid.uuid4().int % 10**6:06d}"),
        ).fetchone()[0])
        c.run_id = str(su.execute(
            "insert into ops.watcher_runs (agent_name, status, stats) "
            "values ('cipo-watcher', 'completed', '{\"rows\": 2}') returning id",
        ).fetchone()[0])
        for mid, detail in ((c.matter, "CIPO 500"), (c.restricted_matter, "restricted 500")):
            su.execute(
                "insert into app.watcher_failures (matter_id, run_id, tag, detail) "
                "values (%s, %s, 'cipo_500', %s)",
                (mid, c.run_id, detail),
            )
    yield c


def test_failures_visible_and_filterable(ctx: Ctx) -> None:
    resp = client.get(
        f"/api/v1/watchers/failures?matter_id={ctx.matter}", headers=_hdr(ADMIN)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["tag"] == "cipo_500"
    assert rows[0]["run_id"] == ctx.run_id


def test_failures_rls_hides_restricted_matter(ctx: Ctx) -> None:
    """STAFF (no family_access, not permissions admin) must not see the restricted matter's
    failures — same visibility rule as the matter itself."""
    resp = client.get("/api/v1/watchers/failures?tag=cipo_500", headers=_hdr(STAFF))
    assert resp.status_code == 200, resp.text
    matter_ids = {r["matter_id"] for r in resp.json()}
    assert ctx.matter in matter_ids
    assert ctx.restricted_matter not in matter_ids


def test_runs_listed_for_dashboard(ctx: Ctx) -> None:
    resp = client.get("/api/v1/watchers/runs?agent_name=cipo-watcher", headers=_hdr(STAFF))
    assert resp.status_code == 200, resp.text
    runs = {r["id"]: r for r in resp.json()}
    assert ctx.run_id in runs
    assert runs[ctx.run_id]["stats"]["rows"] == 2
    assert runs[ctx.run_id]["status"] == "completed"


def test_agent_registered_with_orchestrator(ctx: Ctx) -> None:
    resp = client.get("/api/v1/agents", headers=_hdr(STAFF))
    assert resp.status_code == 200, resp.text
    agents = {a["name"]: a for a in resp.json()}
    assert "cipo-watcher" in agents
    assert "cipo/twocaptcha-api-key" in agents["cipo-watcher"]["allowed_secret_slots"]
    assert "task.create" in agents["cipo-watcher"]["allowed_actions"]