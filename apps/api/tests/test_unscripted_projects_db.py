"""Ad-hoc project launch + NL planner against Postgres (WP 5.6, §M9)."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from py_shared import llm, redaction
from py_shared.config import settings
from py_shared.domain import project_planner as pp
from py_shared.domain import projects as pj

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
MONDAY = date(2026, 7, 20)


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.projects')").fetchone()
            has_planner = conn.execute(
                "select 1 from ops.agents where name = 'a0-planner'"
            ).fetchone()
            return row is not None and row[0] is not None and has_planner is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5.6 (migration 0022 / planner) not applied")


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


class FakeNer:
    @property
    def name(self) -> str:
        return "fake:test"

    def available(self) -> bool:
        return True

    def entities(self, text: str) -> list[tuple[int, int, str]]:
        spans = []
        for value in ("Acme Corp",):
            i = text.find(value)
            while i != -1:
                spans.append((i, i + len(value), "ORG"))
                i = text.find(value, i + 1)
        return spans


class FakeLlm:
    def __init__(self, canned: str) -> None:
        self._canned = canned

    @property
    def provider(self) -> str:
        return llm.PROVIDER_BEDROCK

    @property
    def model(self) -> str:
        return "us.anthropic.claude-sonnet-4-6"

    def complete(self, prompt: str, system: str = "") -> str:
        assert "Acme Corp" not in prompt, "unredacted client name reached the planner LLM"
        return self._canned


PLAN_JSON = (
    '{"tasks": ['
    '{"ref": "draft", "title": "Draft recordal for ORG_001", "role": "agent", "cycle_days": 3},'
    '{"ref": "file", "title": "File with office", "role": "paralegal", "cycle_days": 1}'
    '], "edges": [{"task": "file", "depends_on": "draft"}]}'
)


# --- ad-hoc launch -------------------------------------------------------------


def test_launch_adhoc_creates_a_project_with_no_template(su: psycopg.Connection) -> None:
    tasks = [
        pj.TemplateTask("draft", "Draft", cycle_days=3),
        pj.TemplateTask("file", "File", cycle_days=1),
    ]
    edges = [pj.TemplateEdge("file", "draft")]
    pid = pj.launch_adhoc_project(su, "One-off recordal", tasks, edges, uuid.UUID(ADMIN_ID), MONDAY)
    row = su.execute(
        "select template_id, template_key from app.projects where id = %s", (pid,)
    ).fetchone()
    assert row == (None, None)
    items = su.execute(
        "select task_ref, due_date from app.work_items where project_id = %s order by ordinal",
        (pid,),
    ).fetchall()
    assert [i[0] for i in items] == ["draft", "file"]
    assert items[0][1] < items[1][1]   # chained/scheduled


def test_adhoc_launch_rejects_a_cyclic_plan(su: psycopg.Connection) -> None:
    tasks = [pj.TemplateTask("a", "A"), pj.TemplateTask("b", "B")]
    edges = [pj.TemplateEdge("a", "b"), pj.TemplateEdge("b", "a")]
    with pytest.raises(pj.TemplateInvalid, match="cycle"):
        pj.launch_adhoc_project(su, "Bad", tasks, edges, uuid.UUID(ADMIN_ID), MONDAY)


# --- NL planner ----------------------------------------------------------------


def test_draft_plan_redacts_validates_and_rehydrates(su: psycopg.Connection) -> None:
    tasks, edges = pp.draft_plan(
        su, "Assignment recordal for Acme Corp across CA and US",
        llm_client=FakeLlm(PLAN_JSON), ner_backend=FakeNer(),
    )
    # Rehydrated: the client name is back in the human-facing plan even though the LLM never saw it.
    assert any("Acme Corp" in t.title for t in tasks)
    assert edges == [pp.TemplateEdge("file", "draft")]


def test_draft_plan_leaves_a_redaction_and_egress_audit(su: psycopg.Connection) -> None:
    pp.draft_plan(su, "Recordal for Acme Corp", llm_client=FakeLlm(PLAN_JSON),
                  ner_backend=FakeNer())
    n = su.execute(
        "select count(*) from ops.llm_egress_log "
        "where agent_name = 'a0-planner' and task = 'plan_project'"
    ).fetchone()[0]
    assert n >= 1


def test_a_drafted_plan_can_be_launched(su: psycopg.Connection) -> None:
    """End to end: NL -> plan -> launch a real chained project."""
    tasks, edges = pp.draft_plan(
        su, "Recordal for Acme Corp", llm_client=FakeLlm(PLAN_JSON), ner_backend=FakeNer()
    )
    pid = pj.launch_adhoc_project(su, "Acme recordal", tasks, edges, uuid.UUID(ADMIN_ID), MONDAY)
    n = su.execute(
        "select count(*) from app.work_items where project_id = %s", (pid,)
    ).fetchone()[0]
    assert n == 2


def test_an_empty_description_is_refused(su: psycopg.Connection) -> None:
    with pytest.raises(pp.PlanningError, match="describe"):
        pp.draft_plan(su, "   ", llm_client=FakeLlm(PLAN_JSON), ner_backend=FakeNer())


def test_a_cyclic_llm_plan_is_caught_before_it_reaches_the_user(su: psycopg.Connection) -> None:
    """The model occasionally emits a cycle; validation catches it so the user edits a sound
    plan."""
    cyclic = (
        '{"tasks": [{"ref": "a", "title": "A"}, {"ref": "b", "title": "B"}], '
        '"edges": [{"task": "a", "depends_on": "b"}, {"task": "b", "depends_on": "a"}]}'
    )
    with pytest.raises(pp.PlanningError, match="not sound"):
        pp.draft_plan(su, "something", llm_client=FakeLlm(cyclic), ner_backend=FakeNer())


def test_planner_fails_closed_without_ner(su: psycopg.Connection) -> None:
    """The planner is not exempt from the D45 egress gate."""
    class Dead(FakeNer):
        def available(self) -> bool:
            return False

    with pytest.raises(redaction.RedactionUnavailable):
        pp.draft_plan(su, "Recordal for Acme Corp", llm_client=FakeLlm(PLAN_JSON),
                      ner_backend=Dead())
