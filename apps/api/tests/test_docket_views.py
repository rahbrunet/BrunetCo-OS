"""WP 1.4 docket views (M1-R5) — live-Postgres API tests.

Covers: due-window and overdue filtering on the effective due date (respond_by, else
final_due_date), assignee/matter filters, ordering, and RLS (restricted-family tasks absent
from plain staff's docket).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"
STAFF_ID = "22222222-2222-2222-2222-222222222222"

TODAY = date.today()


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            has = conn.execute("select to_regclass('app.task_provenance')").fetchone()[0]
            seeded = conn.execute(
                "select 1 from app.os_users where id = %s", (STAFF_ID,)
            ).fetchone()
            return has is not None and seeded is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="seeded Postgres (WP1.4) not reachable")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Ctx:
    family_id: str
    matter_id: str
    matter_ref: str
    restricted_matter: str
    t_overdue: str
    t_soon: str       # respond_by in 3 days
    t_far: str        # final_due_date in 60 days
    t_other_matter: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'View Co') returning id",
            (f"V{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        c.family_id = str(su.execute(
            """
            insert into app.families (client_id, family_seq, reference, title, family_type)
            values (%s, '0001', %s, 'Viewable', 'patent') returning id
            """,
            (client_id, f"V-{suffix}"),
        ).fetchone()[0])
        c.matter_ref = f"V-{suffix}-CA"
        c.matter_id = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (c.family_id, c.matter_ref),
        ).fetchone()[0])
        other_matter = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'US', 'US') returning id
            """,
            (c.family_id, f"V-{suffix}-US"),
        ).fetchone()[0])
        restricted_family = su.execute(
            """
            insert into app.families
              (client_id, family_seq, reference, title, family_type, restricted)
            values (%s, '0002', %s, 'Hidden', 'patent', true) returning id
            """,
            (client_id, f"VR-{suffix}"),
        ).fetchone()[0]
        c.restricted_matter = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (restricted_family, f"VR-{suffix}-CA"),
        ).fetchone()[0])

        def _task(matter: str, title: str, respond_by: date | None,
                  final_due: date | None, assignee: str | None = STAFF_ID) -> str:
            return str(su.execute(
                """
                insert into app.tasks
                  (matter_id, title, deadline_type, respond_by, final_due_date, assignee_id)
                values (%s, %s, 'hard_external', %s, %s, %s) returning id
                """,
                (matter, title, respond_by, final_due, assignee),
            ).fetchone()[0])

        c.t_overdue = _task(c.matter_id, "Overdue OA", TODAY - timedelta(days=5), None)
        c.t_soon = _task(c.matter_id, "Due soon", TODAY + timedelta(days=3),
                         TODAY + timedelta(days=30))
        c.t_far = _task(c.matter_id, "Far away", None, TODAY + timedelta(days=60))
        c.t_other_matter = _task(other_matter, "Other matter", TODAY + timedelta(days=3), None,
                                 assignee=None)
        _task(c.restricted_matter, "Secret task", TODAY + timedelta(days=1), None)
    yield c


def _view(token: str, query: str) -> list[dict]:
    resp = client.get(f"/api/v1/docket/tasks?{query}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_matter_docket_ordered_by_effective_due(ctx: Ctx) -> None:
    rows = _view(STAFF, f"matter_id={ctx.matter_id}")
    assert [r["id"] for r in rows] == [ctx.t_overdue, ctx.t_soon, ctx.t_far]
    assert rows[0]["matter_reference"] == ctx.matter_ref


def test_overdue_filter(ctx: Ctx) -> None:
    rows = _view(STAFF, f"matter_id={ctx.matter_id}&overdue=true")
    assert [r["id"] for r in rows] == [ctx.t_overdue]


def test_due_window_uses_effective_due_date(ctx: Ctx) -> None:
    # Window ending in 7 days: catches overdue (-5d) and soon (+3d); far (+60d) excluded.
    due_to = (TODAY + timedelta(days=7)).isoformat()
    rows = _view(STAFF, f"matter_id={ctx.matter_id}&due_to={due_to}")
    assert {r["id"] for r in rows} == {ctx.t_overdue, ctx.t_soon}


def test_assignee_filter(ctx: Ctx) -> None:
    rows = _view(STAFF, f"family_id={ctx.family_id}&assignee_id={STAFF_ID}")
    assert {r["id"] for r in rows} == {ctx.t_overdue, ctx.t_soon, ctx.t_far}


def test_restricted_family_tasks_absent_for_staff_present_for_admin(ctx: Ctx) -> None:
    staff_rows = _view(STAFF, f"matter_id={ctx.restricted_matter}")
    admin_rows = _view(ADMIN, f"matter_id={ctx.restricted_matter}")
    assert staff_rows == []
    assert len(admin_rows) == 1
