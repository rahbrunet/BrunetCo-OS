"""WP 5.5-lite My Day — unified, due-ordered, per-user queue of docket tasks + work items."""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings

STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"
STAFF_ID = "22222222-2222-2222-2222-222222222222"
ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
ADMIN_ID = "11111111-1111-1111-1111-111111111111"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            seeded = conn.execute(
                "select 1 from app.os_users where id = %s", (STAFF_ID,)
            ).fetchone()
            return seeded is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="seeded Postgres not reachable")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


class Ctx:
    matter: str
    tag: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    c.tag = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'MyDay Co') returning id",
            (f"M{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        fam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (client_id, f"M-{c.tag}"),
        ).fetchone()[0]
        c.matter = str(su.execute(
            "insert into app.matters"
            " (family_id, reference, jurisdiction_code, jurisdiction_segment)"
            " values (%s, %s, 'CA', 'CA') returning id",
            (fam, f"M-{c.tag}-CA"),
        ).fetchone()[0])
        # Two docket tasks for STAFF (different due dates) + one for ADMIN (must not appear).
        su.execute(
            "insert into app.tasks (matter_id, title, deadline_type, status, final_due_date,"
            " assignee_id) values (%s, %s, 'hard_external', 'open', '2026-09-01', %s)",
            (c.matter, f"Later task {c.tag}", STAFF_ID),
        )
        su.execute(
            "insert into app.tasks (matter_id, title, deadline_type, status, respond_by,"
            " assignee_id) values (%s, %s, 'extendable_external', 'open', '2026-08-01', %s)",
            (c.matter, f"Sooner task {c.tag}", STAFF_ID),
        )
        su.execute(
            "insert into app.tasks (matter_id, title, deadline_type, status, final_due_date,"
            " assignee_id) values (%s, %s, 'internal', 'open', '2026-07-15', %s)",
            (c.matter, f"Admin task {c.tag}", ADMIN_ID),
        )
        # A work item for STAFF.
        su.execute(
            "insert into app.work_items (title, matter_id, assignee_id, status, due_date,"
            " created_by) values (%s, %s, %s, 'open', '2026-08-15', %s)",
            (f"Work item {c.tag}", c.matter, STAFF_ID, STAFF_ID),
        )
    yield c


def test_my_day_unions_tasks_and_work_items_for_the_caller(ctx: Ctx) -> None:
    items = client.get("/api/v1/my-day", headers=_hdr(STAFF)).json()
    mine = [i for i in items if ctx.tag in i["title"]]
    titles = {i["title"] for i in mine}
    assert f"Sooner task {ctx.tag}" in titles
    assert f"Later task {ctx.tag}" in titles
    assert f"Work item {ctx.tag}" in titles
    assert f"Admin task {ctx.tag}" not in titles      # assigned to someone else
    sources = {i["source"] for i in mine}
    assert sources == {"docket", "work_item"}


def test_my_day_is_due_ordered(ctx: Ctx) -> None:
    items = [i for i in client.get("/api/v1/my-day", headers=_hdr(STAFF)).json()
             if ctx.tag in i["title"]]
    dues = [i["due_date"] for i in items]
    assert dues == sorted(dues)                       # ascending due date
    assert items[0]["title"] == f"Sooner task {ctx.tag}"   # 2026-08-01 first


def test_my_day_is_per_user(ctx: Ctx) -> None:
    # ADMIN sees their own task, not STAFF's.
    admin_items = client.get("/api/v1/my-day", headers=_hdr(ADMIN)).json()
    admin_titles = {i["title"] for i in admin_items if ctx.tag in i["title"]}
    assert admin_titles == {f"Admin task {ctx.tag}"}
