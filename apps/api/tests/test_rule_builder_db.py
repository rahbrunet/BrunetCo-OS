"""Rule Builder against Postgres (WP 6.6, §A2) — dry run, round-trip, egress audit.

The dry-run test that asserts *nothing was written* is the load-bearing one: M1-R4 exists so a
practitioner can point a candidate rule at real matters without risk.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from py_shared import llm, redaction
from py_shared.config import settings
from py_shared.domain import rule_builder as rb

ADMIN_ID = "11111111-1111-1111-1111-111111111111"

DRAFT_JSON = (
    '{"trigger_code": "office_action", "definition": {'
    '"title": "Respond to Office Action", "deadline_type": "extendable_external", '
    '"offsets": {"respond_by": {"months": 4}, "final_due_date": {"months": 6}}, '
    '"business_day_roll": true}}'
)


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute(
                "select 1 from ops.agents where name = 'a2-rule-builder'"
            ).fetchone()
            return row is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.6 migration (0025) not applied")


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
        return []


class FakeLlm:
    def __init__(self, canned: str = DRAFT_JSON) -> None:
        self._canned = canned

    @property
    def provider(self) -> str:
        return llm.PROVIDER_BEDROCK

    @property
    def model(self) -> str:
        return "us.anthropic.claude-sonnet-4-6"

    def complete(self, prompt: str, system: str = "") -> str:
        return self._canned


# --- the round-trip ------------------------------------------------------------


def test_a_drafted_rule_reads_back_in_plain_english(su: psycopg.Connection) -> None:
    """The practitioner confirms the model understood them by reading a sentence, not JSON."""
    drafted = rb.draft_rule(
        su, "Four months to respond to an office action, six months final, extendable",
        llm_client=FakeLlm(), ner_backend=FakeNer(),
    )
    assert "Respond to Office Action" in drafted.reads_as
    assert "4 months" in drafted.reads_as
    assert drafted.definition["deadline_type"] == "extendable_external"


def test_a_draft_arrives_with_test_cases_already_computed(su: psycopg.Connection) -> None:
    """A human reviews dates, not abstractions (M1-R4)."""
    drafted = rb.draft_rule(su, "four months to respond", llm_client=FakeLlm(),
                            ner_backend=FakeNer())
    assert drafted.test_cases
    assert all("respond_by" in case.dates for case in drafted.test_cases)


def test_an_unsound_drafted_rule_is_refused_before_the_human_sees_it(
    su: psycopg.Connection,
) -> None:
    """The model occasionally inverts the offsets; that must not reach a review screen looking
    plausible."""
    inverted = (
        '{"trigger_code": "x", "definition": {"title": "Bad", '
        '"deadline_type": "hard_external", '
        '"offsets": {"respond_by": {"months": 8}, "final_due_date": {"months": 6}}}}'
    )
    with pytest.raises(rb.RuleDraftError, match="falls after"):
        rb.draft_rule(su, "something", llm_client=FakeLlm(inverted), ner_backend=FakeNer())


def test_an_empty_description_is_refused(su: psycopg.Connection) -> None:
    with pytest.raises(rb.RuleDraftError, match="describe"):
        rb.draft_rule(su, "  ", llm_client=FakeLlm(), ner_backend=FakeNer())


def test_drafting_leaves_an_egress_audit(su: psycopg.Connection) -> None:
    rb.draft_rule(su, "four months to respond", llm_client=FakeLlm(), ner_backend=FakeNer())
    n = su.execute(
        "select count(*) from ops.llm_egress_log "
        "where agent_name = 'a2-rule-builder' and task = 'draft_rule'"
    ).fetchone()[0]
    assert n >= 1


def test_the_rule_builder_fails_closed_without_ner(su: psycopg.Connection) -> None:
    class Dead(FakeNer):
        def available(self) -> bool:
            return False

    with pytest.raises(redaction.RedactionUnavailable):
        rb.draft_rule(su, "four months", llm_client=FakeLlm(), ner_backend=Dead())


def test_a2_holds_no_allowed_actions(su: psycopg.Connection) -> None:
    """A2 proposes rules; it never installs one. The empty allow-list states that at the registry
    level, not just in code."""
    actions = su.execute(
        "select allowed_actions from ops.agents where name = 'a2-rule-builder'"
    ).fetchone()[0]
    assert actions == []


# --- dry run (M1-R4) -----------------------------------------------------------


@pytest.fixture()
def matter(su: psycopg.Connection) -> Iterator[str]:
    cid = su.execute(
        "insert into app.clients (code, name) values (%s, 'Dry Run Co') returning id",
        (f"D{uuid.uuid4().hex[:5].upper()}",),
    ).fetchone()[0]
    fid = su.execute(
        "insert into app.families (client_id, family_seq, reference, title, family_type) "
        "values (%s, '0001', %s, 'F', 'patent') returning id",
        (cid, f"F{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    mid = su.execute(
        "insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment, "
        "status, filing_date) values (%s, %s, 'CA', 'CA', 'pending', '2026-01-15') returning id",
        (fid, f"M{uuid.uuid4().hex[:5]}"),
    ).fetchone()[0]
    yield str(mid)
    su.execute("delete from app.matters where id = %s", (mid,))
    su.execute("delete from app.families where id = %s", (fid,))
    su.execute("delete from app.clients where id = %s", (cid,))


GOOD_DEF = {
    "title": "Respond to Office Action",
    "deadline_type": "extendable_external",
    "offsets": {"respond_by": {"months": 4}},
    "business_day_roll": True,
}


def test_dry_run_previews_against_real_matters(su: psycopg.Connection, matter: str) -> None:
    preview = rb.dry_run(su, GOOD_DEF, "office_action", jurisdiction_code="CA")
    assert preview
    row = next((p for p in preview if str(p["matter_id"]) == matter), None)
    assert row is not None
    assert row["dates"]["respond_by"] == date(2026, 5, 15)


def test_dry_run_writes_absolutely_nothing(su: psycopg.Connection, matter: str) -> None:
    """M1-R4: point a candidate rule at real matters without risk."""
    before_tasks = su.execute("select count(*) from app.tasks").fetchone()[0]
    before_prov = su.execute("select count(*) from app.task_provenance").fetchone()[0]
    rb.dry_run(su, GOOD_DEF, "office_action", jurisdiction_code="CA")
    assert su.execute("select count(*) from app.tasks").fetchone()[0] == before_tasks
    assert su.execute("select count(*) from app.task_provenance").fetchone()[0] == before_prov


def test_dry_run_respects_the_jurisdiction_filter(su: psycopg.Connection, matter: str) -> None:
    preview = rb.dry_run(su, GOOD_DEF, "office_action", jurisdiction_code="US")
    assert all(str(p["matter_id"]) != matter for p in preview)


def test_dry_run_validates_before_touching_the_database(su: psycopg.Connection) -> None:
    with pytest.raises(rb.RuleDraftError):
        rb.dry_run(su, {**GOOD_DEF, "offsets": {}}, "office_action")
