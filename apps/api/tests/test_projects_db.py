"""Template versioning, project launch and chaining against Postgres (WP 5.1, §M9).

The versioning tests carry the weight. A project running under "OA response v3" must stay
explainable after v4 changes the stages — otherwise nobody can answer why a task exists, which
is the failure mode that made the Word project lists untrustworthy in the first place.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import projects as pj

ADMIN_ID = "11111111-1111-1111-1111-111111111111"   # Principal — permissions admin
STAFF_ID = "22222222-2222-2222-2222-222222222222"   # Agent

MONDAY = date(2026, 7, 20)


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.project_templates')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.1 migration (0018) not applied")


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def template(su: psycopg.Connection) -> Iterator[str]:
    """A three-task chain: draft -> review -> file."""
    key = f"tpl-{uuid.uuid4().hex[:8]}"
    row = su.execute(
        "insert into app.project_templates (key, version, name, created_by) "
        "values (%s, 1, 'OA Response', %s) returning id",
        (key, ADMIN_ID),
    ).fetchone()
    assert row is not None
    template_id = str(row[0])

    stage = su.execute(
        "insert into app.project_template_stages (template_id, ordinal, name) "
        "values (%s, 1, 'Preparation') returning id",
        (template_id,),
    ).fetchone()
    assert stage is not None

    for ordinal, (ref, title, role, cycle) in enumerate([
        ("draft", "Draft response", "agent", 5),
        ("review", "Review response", "principal", 2),
        ("file", "File response", "paralegal", 1),
    ]):
        su.execute(
            "insert into app.project_template_tasks "
            "  (template_id, stage_id, task_ref, title, role, cycle_days, ordinal) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (template_id, stage[0], ref, title, role, cycle, ordinal),
        )
    su.execute(
        "insert into app.project_template_dependencies (template_id, task_ref, depends_on_ref) "
        "values (%s, 'review', 'draft'), (%s, 'file', 'review')",
        (template_id, template_id),
    )
    yield template_id
    su.execute("delete from app.project_templates where key = %s", (key,))


# --- publishing ----------------------------------------------------------------


def test_publishing_validates_and_marks_published(
    su: psycopg.Connection, template: str,
) -> None:
    pj.publish_template(su, uuid.UUID(template))
    row = su.execute(
        "select status::text from app.project_templates where id = %s", (template,)
    ).fetchone()
    assert row is not None and row[0] == "published"


def test_publishing_a_cyclic_template_is_refused(
    su: psycopg.Connection, template: str,
) -> None:
    """Validation runs at publish, not at authoring — a half-built draft is legitimately broken."""
    su.execute(
        "insert into app.project_template_dependencies (template_id, task_ref, depends_on_ref) "
        "values (%s, 'draft', 'file')",
        (template,),
    )
    with pytest.raises(pj.TemplateInvalid, match="cycle"):
        pj.publish_template(su, uuid.UUID(template))


def test_publishing_a_new_version_retires_the_old_one(
    su: psycopg.Connection, template: str,
) -> None:
    pj.publish_template(su, uuid.UUID(template))
    v2 = pj.new_version(su, uuid.UUID(template), uuid.UUID(ADMIN_ID))
    pj.publish_template(su, v2)

    statuses = dict(su.execute(
        "select version, status::text from app.project_templates "
        " where key = (select key from app.project_templates where id = %s)",
        (template,),
    ).fetchall())
    assert statuses[1] == "retired"
    assert statuses[2] == "published"


def test_only_one_version_may_be_published_at_a_time(
    su: psycopg.Connection, template: str,
) -> None:
    """"Which version does a new project get?" must have exactly one answer."""
    pj.publish_template(su, uuid.UUID(template))
    v2 = pj.new_version(su, uuid.UUID(template), uuid.UUID(ADMIN_ID))
    with pytest.raises(psycopg.errors.UniqueViolation):
        su.execute(
            "update app.project_templates set status = 'published' where id = %s", (v2,)
        )


def test_new_version_clones_tasks_stages_and_edges(
    su: psycopg.Connection, template: str,
) -> None:
    v2 = pj.new_version(su, uuid.UUID(template), uuid.UUID(ADMIN_ID))
    tasks, edges = pj.load_template(su, v2)
    assert {t.task_ref for t in tasks} == {"draft", "review", "file"}
    assert len(edges) == 2
    assert all(t.stage == "Preparation" for t in tasks)


# --- launching -----------------------------------------------------------------


def test_launching_a_draft_template_is_refused(
    su: psycopg.Connection, template: str,
) -> None:
    """A draft is unvalidated by definition; a project built from one can contain a cycle."""
    with pytest.raises(ValueError, match="not published"):
        pj.launch_project(su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY)


def test_launch_creates_scheduled_chained_work_items(
    su: psycopg.Connection, template: str,
) -> None:
    pj.publish_template(su, uuid.UUID(template))
    project_id = pj.launch_project(
        su, uuid.UUID(template), "OA response — matter X", uuid.UUID(ADMIN_ID), MONDAY
    )
    rows = su.execute(
        "select task_ref, due_date, stage_name, role from app.work_items "
        " where project_id = %s order by ordinal",
        (project_id,),
    ).fetchall()
    assert [r[0] for r in rows] == ["draft", "review", "file"]
    assert rows[0][1] < rows[1][1] < rows[2][1]      # sequential, chained
    assert rows[0][2] == "Preparation"
    assert rows[0][3] == "agent"


def test_launch_records_the_template_version_it_followed(
    su: psycopg.Connection, template: str,
) -> None:
    """The M1-R14 discipline applied to projects: the version is recorded, not inferred."""
    pj.publish_template(su, uuid.UUID(template))
    project_id = pj.launch_project(
        su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY
    )
    row = su.execute(
        "select template_version from app.projects where id = %s", (project_id,)
    ).fetchone()
    assert row is not None and row[0] == 1


def test_an_in_flight_project_is_unaffected_by_a_new_template_version(
    su: psycopg.Connection, template: str,
) -> None:
    """The whole reason templates are versioned rather than edited in place."""
    pj.publish_template(su, uuid.UUID(template))
    project_id = pj.launch_project(su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY)

    v2 = pj.new_version(su, uuid.UUID(template), uuid.UUID(ADMIN_ID))
    su.execute("delete from app.project_template_dependencies where template_id = %s", (v2,))
    su.execute(
        "delete from app.project_template_tasks where template_id = %s and task_ref = 'review'",
        (v2,),
    )
    pj.publish_template(su, v2)

    refs = [r[0] for r in su.execute(
        "select task_ref from app.work_items where project_id = %s", (project_id,)
    ).fetchall()]
    assert "review" in refs


def test_role_routing_assigns_from_the_role_table(
    su: psycopg.Connection, template: str,
) -> None:
    su.execute(
        "insert into app.role_assignments (role, user_id) values ('agent', %s) "
        "on conflict (role) do update set user_id = excluded.user_id",
        (STAFF_ID,),
    )
    try:
        pj.publish_template(su, uuid.UUID(template))
        project_id = pj.launch_project(su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY)
        row = su.execute(
            "select assignee_id from app.work_items where project_id = %s and task_ref = 'draft'",
            (project_id,),
        ).fetchone()
        assert row is not None and str(row[0]) == STAFF_ID
    finally:
        su.execute("delete from app.role_assignments where role = 'agent'")


def test_an_unrouted_role_leaves_the_item_unassigned(
    su: psycopg.Connection, template: str,
) -> None:
    """Better an obviously unassigned task than one silently assigned to whoever launched it."""
    pj.publish_template(su, uuid.UUID(template))
    project_id = pj.launch_project(su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY)
    row = su.execute(
        "select assignee_id from app.work_items where project_id = %s and task_ref = 'file'",
        (project_id,),
    ).fetchone()
    assert row is not None and row[0] is None


# --- chaining ------------------------------------------------------------------


@pytest.fixture()
def launched(su: psycopg.Connection, template: str) -> str:
    pj.publish_template(su, uuid.UUID(template))
    return str(pj.launch_project(su, uuid.UUID(template), "P", uuid.UUID(ADMIN_ID), MONDAY))


def _item(su: psycopg.Connection, project_id: str, ref: str) -> uuid.UUID:
    row = su.execute(
        "select id from app.work_items where project_id = %s and task_ref = %s",
        (project_id, ref),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def test_a_successor_starts_blocked(su: psycopg.Connection, launched: str) -> None:
    row = su.execute(
        "select is_blocked from app.work_item_queue where id = %s",
        (_item(su, launched, "review"),),
    ).fetchone()
    assert row is not None and row[0] is True


def test_the_first_task_is_not_blocked(su: psycopg.Connection, launched: str) -> None:
    row = su.execute(
        "select is_blocked from app.work_item_queue where id = %s",
        (_item(su, launched, "draft"),),
    ).fetchone()
    assert row is not None and row[0] is False


def test_completing_a_task_unblocks_its_successor(
    su: psycopg.Connection, launched: str,
) -> None:
    unblocked = pj.complete_work_item(su, _item(su, launched, "draft"))
    assert unblocked == [_item(su, launched, "review")]


def test_completing_does_not_unblock_further_down_the_chain(
    su: psycopg.Connection, launched: str,
) -> None:
    pj.complete_work_item(su, _item(su, launched, "draft"))
    row = su.execute(
        "select is_blocked from app.work_item_queue where id = %s",
        (_item(su, launched, "file"),),
    ).fetchone()
    assert row is not None and row[0] is True


def test_a_blocked_task_cannot_be_completed(su: psycopg.Connection, launched: str) -> None:
    with pytest.raises(ValueError, match="blocked"):
        pj.complete_work_item(su, _item(su, launched, "review"))


def test_a_cancelled_predecessor_does_not_block_forever(
    su: psycopg.Connection, launched: str,
) -> None:
    """Cancelling is a decision the task will not happen; stranding its successors would leave
    the project unfinishable without editing the graph."""
    su.execute(
        "update app.work_items set status = 'cancelled' where id = %s",
        (_item(su, launched, "draft"),),
    )
    row = su.execute(
        "select is_blocked from app.work_item_queue where id = %s",
        (_item(su, launched, "review"),),
    ).fetchone()
    assert row is not None and row[0] is False


def test_completing_twice_is_refused(su: psycopg.Connection, launched: str) -> None:
    pj.complete_work_item(su, _item(su, launched, "draft"))
    with pytest.raises(LookupError):
        pj.complete_work_item(su, _item(su, launched, "draft"))


def test_progress_counts_by_status(su: psycopg.Connection, launched: str) -> None:
    pj.complete_work_item(su, _item(su, launched, "draft"))
    progress = pj.project_progress(su, uuid.UUID(launched))
    assert progress["done"] == 1
    assert progress["open"] == 2


def test_the_full_graph_exists_from_launch(su: psycopg.Connection, launched: str) -> None:
    """Nothing is spawned on completion — the plan is stable and reviewable from day one."""
    row = su.execute(
        "select count(*) from app.work_items where project_id = %s", (launched,)
    ).fetchone()
    assert row is not None and row[0] == 3


# --- RLS -----------------------------------------------------------------------


def test_staff_can_read_templates(su: psycopg.Connection, template: str) -> None:
    """You cannot launch what you cannot see."""
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.project_templates where id = %s", (template,)
        ).fetchone()
        assert row is not None and row[0] == 1


def test_publishing_is_admin_gated(su: psycopg.Connection, template: str) -> None:
    """A published template silently changes what every future project does."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _user_conn(STAFF_ID) as conn:
        conn.execute(
            "update app.project_templates set status = 'published' where id = %s", (template,)
        )


def test_role_routing_changes_are_admin_gated(su: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _user_conn(STAFF_ID) as conn:
        conn.execute(
            "insert into app.role_assignments (role, user_id) values ('rogue', %s)", (STAFF_ID,)
        )
