"""WP 1.2 docketing engine — API tests against live Postgres (test_matter_crud pattern).

Covers: trigger firing with jurisdiction scoping and rule versioning, dual dates with holiday
roll recorded in the M1-R14 provenance record, task-completion chaining, provenance search +
CSV export, RLS-scoped visibility, and provenance immutability (no update/delete for anyone).
"""
from __future__ import annotations

import json
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


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            has = conn.execute("select to_regclass('app.task_provenance')").fetchone()[0]
            seeded = conn.execute(
                "select 1 from app.os_users where id = %s", (ADMIN_ID,)
            ).fetchone()
            return has is not None and seeded is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="seeded Postgres (WP1.2) not reachable")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _rule(su: psycopg.Connection, trigger: str, definition: dict, *,
          jurisdiction: str | None = None, rule_id: str | None = None, version: int = 1,
          effective_from: str = "2020-01-01", active: bool = True) -> str:
    rid = rule_id or str(uuid.uuid4())
    su.execute(
        """
        insert into app.docket_rules
          (rule_id, version, name, trigger_code, jurisdiction_code, definition, active,
           effective_from, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (rid, version, definition["title"], trigger, jurisdiction, json.dumps(definition),
         active, effective_from, ADMIN_ID),
    )
    return rid


class Ctx:
    client_id: str
    family_id: str
    matter_ca: str       # jurisdiction CA
    matter_us: str       # jurisdiction US
    restricted_matter: str
    rule_oa: str         # CA office-action rule, v1+v2
    rule_chain_parent: str
    rule_chain_child: str
    trigger_oa: str
    trigger_chain: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    c.trigger_oa = f"office_action:{suffix}"
    c.trigger_chain = f"filing:{suffix}"
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        c.client_id = str(su.execute(
            "insert into app.clients (code, name) values (%s, 'Docket Co') returning id",
            (f"K{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0])
        c.family_id = str(su.execute(
            """
            insert into app.families (client_id, family_seq, reference, title, family_type)
            values (%s, '0001', %s, 'Widget', 'patent') returning id
            """,
            (c.client_id, f"K-{suffix}"),
        ).fetchone()[0])
        c.matter_ca = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (c.family_id, f"K-{suffix}-CA"),
        ).fetchone()[0])
        c.matter_us = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'US', 'US') returning id
            """,
            (c.family_id, f"K-{suffix}-US"),
        ).fetchone()[0])
        # Restricted family + matter (no ACLs at all → only the permissions admin sees it).
        restricted_family = su.execute(
            """
            insert into app.families
              (client_id, family_seq, reference, title, family_type, restricted)
            values (%s, '0002', %s, 'Secret', 'patent', true) returning id
            """,
            (c.client_id, f"KR-{suffix}"),
        ).fetchone()[0]
        c.restricted_matter = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (restricted_family, f"KR-{suffix}-CA"),
        ).fetchone()[0])

        # CA holiday used by the roll test: Canada Day 2026 (Wed).
        su.execute(
            """
            insert into app.holidays (jurisdiction_code, holiday_date, name)
            values ('CA', '2026-07-01', 'Canada Day') on conflict do nothing
            """
        )

        # CA-scoped office-action rule, two versions: v1 (4 months), v2 (6 + 10 months,
        # effective 2026-01-01). The engine must pick the version in force on the ref date.
        c.rule_oa = _rule(su, c.trigger_oa, {
            "title": "Respond to Office Action (v1)",
            "deadline_type": "extendable_external",
            "offsets": {"respond_by": {"months": 4}},
        }, jurisdiction="CA")
        _rule(su, c.trigger_oa, {
            "title": "Respond to Office Action",
            "deadline_type": "extendable_external",
            "offsets": {"respond_by": {"months": 6}, "final_due_date": {"months": 10}},
        }, jurisdiction="CA", rule_id=c.rule_oa, version=2, effective_from="2026-01-01")

        # Chain: filing → "File declaration" task whose completion fires a follow-on reminder.
        c.rule_chain_parent = _rule(su, c.trigger_chain, {
            "title": "File declaration",
            "deadline_type": "internal",
            "offsets": {"final_due_date": {"months": 1}},
            "completion_code": f"declaration:{suffix}",
        })
        c.rule_chain_child = _rule(su, f"task_completed:declaration:{suffix}", {
            "title": "Confirm declaration recorded",
            "deadline_type": "general_reminder",
            "offsets": {"final_due_date": {"days": 14}},
        })
    yield c


def _fire(token: str, matter_id: str, code: str, ref: str) -> list[dict]:
    resp = client.post(
        "/api/v1/docket/trigger",
        json={"matter_id": matter_id, "trigger_code": code, "ref_date": ref},
        headers=_hdr(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Trigger firing, versioning, jurisdiction scoping ------------------------

def test_trigger_generates_task_with_dual_dates(ctx: Ctx) -> None:
    tasks = _fire(STAFF, ctx.matter_ca, ctx.trigger_oa, "2026-03-10")
    assert len(tasks) == 1
    t = tasks[0]
    # v2 in force on 2026-03-10: 6m → Thu 2026-09-10; 10m → Mon 2027-01-11 (10 Jan = Sunday).
    assert t["rule_version"] == 2
    assert t["respond_by"] == "2026-09-10"
    assert t["final_due_date"] == "2027-01-11"


def test_rule_version_selected_by_ref_date(ctx: Ctx) -> None:
    # Ref date before v2's effective_from → v1 (4 months, no final_due_date).
    tasks = _fire(STAFF, ctx.matter_ca, ctx.trigger_oa, "2025-06-02")
    assert len(tasks) == 1
    assert tasks[0]["rule_version"] == 1
    assert tasks[0]["respond_by"] == "2025-10-02"
    assert tasks[0]["final_due_date"] is None


def test_jurisdiction_scoped_rule_skips_other_matters(ctx: Ctx) -> None:
    # The OA rule is CA-scoped; firing the same trigger on the US matter generates nothing.
    assert _fire(STAFF, ctx.matter_us, ctx.trigger_oa, "2026-03-10") == []


def test_holiday_roll_recorded_in_provenance(ctx: Ctx) -> None:
    # v2 (effective 2026-01-01) fires from ref 2026-01-01: +6m = Wed 2026-07-01 = Canada Day
    # → rolls to Thu 2026-07-02.
    tasks = _fire(STAFF, ctx.matter_ca, ctx.trigger_oa, "2026-01-01")
    t = tasks[0]
    assert t["rule_version"] == 2
    assert t["respond_by"] == "2026-07-02"
    prov = client.get(
        f"/api/v1/docket/provenance?matter_id={ctx.matter_ca}", headers=_hdr(STAFF)
    ).json()
    rec = next(p for p in prov if p["task_id"] == t["task_id"])
    calc = rec["calculated_dates"]["respond_by"]
    assert calc["raw"] == "2026-07-01"
    assert calc["rolled"] == "2026-07-02"
    assert calc["trace"][0]["reason"] == "Canada Day"
    assert rec["input_dates"] == {"ref_date": "2026-01-01"}
    assert rec["rule_id"] == ctx.rule_oa


def test_completion_chains_follow_on_rule(ctx: Ctx) -> None:
    tasks = _fire(STAFF, ctx.matter_ca, ctx.trigger_chain, "2026-02-02")
    parent = tasks[0]
    assert parent["title"] == "File declaration"
    resp = client.post(
        f"/api/v1/docket/tasks/{parent['task_id']}/complete",
        json={"closed_on": "2026-02-16"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 200, resp.text
    chained = resp.json()["chained"]
    assert len(chained) == 1
    assert chained[0]["title"] == "Confirm declaration recorded"
    assert chained[0]["final_due_date"] == "2026-03-02"  # +14d = Mon 2026-03-02
    # Chained provenance records the completed task as its trigger.
    prov = client.get(
        f"/api/v1/docket/provenance?matter_id={ctx.matter_ca}&trigger_type=task_completion",
        headers=_hdr(STAFF),
    ).json()
    rec = next(p for p in prov if p["task_id"] == chained[0]["task_id"])
    assert rec["trigger_id"] == parent["task_id"]


def test_completing_closed_task_is_404(ctx: Ctx) -> None:
    tasks = _fire(STAFF, ctx.matter_ca, ctx.trigger_chain, "2026-04-01")
    tid = tasks[0]["task_id"]
    first = client.post(
        f"/api/v1/docket/tasks/{tid}/complete", json={"closed_on": "2026-04-10"},
        headers=_hdr(STAFF),
    )
    assert first.status_code == 200
    again = client.post(
        f"/api/v1/docket/tasks/{tid}/complete", json={"closed_on": "2026-04-11"},
        headers=_hdr(STAFF),
    )
    assert again.status_code == 404


# --- Provenance query + CSV --------------------------------------------------

def test_provenance_csv_export(ctx: Ctx) -> None:
    resp = client.get(
        f"/api/v1/docket/provenance.csv?matter_id={ctx.matter_ca}", headers=_hdr(STAFF)
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("id,task_id,matter_id")
    assert len(lines) >= 2  # header + at least one record from earlier tests


# --- RLS ---------------------------------------------------------------------

def test_trigger_on_restricted_matter_hidden_from_staff(ctx: Ctx) -> None:
    resp = client.post(
        "/api/v1/docket/trigger",
        json={"matter_id": ctx.restricted_matter, "trigger_code": ctx.trigger_oa,
              "ref_date": "2026-03-10"},
        headers=_hdr(STAFF),
    )
    assert resp.status_code == 404  # matter invisible at the DB layer


def test_admin_can_fire_on_restricted_matter(ctx: Ctx) -> None:
    tasks = _fire(ADMIN, ctx.restricted_matter, ctx.trigger_oa, "2026-03-10")
    assert len(tasks) == 1
    # And its provenance is invisible to plain staff.
    staff_view = client.get(
        f"/api/v1/docket/provenance?matter_id={ctx.restricted_matter}", headers=_hdr(STAFF)
    ).json()
    assert staff_view == []
    admin_view = client.get(
        f"/api/v1/docket/provenance?matter_id={ctx.restricted_matter}", headers=_hdr(ADMIN)
    ).json()
    assert len(admin_view) == 1


# --- Immutability (M1-R14: permanent, part of the audit trail) ---------------

def test_provenance_is_immutable_even_for_admin(ctx: Ctx) -> None:
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=ADMIN_ID, email="a@t.local"))
    # UPDATE and DELETE are not even granted to `authenticated` (belt) and no RLS policy
    # exists for them (suspenders) → both statements fail outright, for the admin too.
    with pytest.raises(psycopg.Error):
        with user_connection(jwt) as conn:
            conn.execute("update app.task_provenance set trigger_id = 'tampered'")
    with pytest.raises(psycopg.Error):
        with user_connection(jwt) as conn:
            conn.execute("delete from app.task_provenance")
