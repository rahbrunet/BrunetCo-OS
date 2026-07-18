"""Provider routing (WP 6.9 §1, D45) — sensitivity decides, and the Fireworks gate defaults shut.

Pure: no AWS, no Fireworks, no network. Provider *selection* is the security-relevant behaviour;
the HTTP calls themselves are ordinary client code.
"""
from __future__ import annotations

import pytest
from py_shared import llm
from py_shared.config import settings


@pytest.fixture(autouse=True)
def _restore_gate() -> object:
    """The gate is global state; a test that flips it must not leak into the next one."""
    original = settings.llm_fireworks_enabled
    yield
    settings.llm_fireworks_enabled = original


def test_client_facing_tasks_route_to_bedrock() -> None:
    assert llm.provider_for("draft_reply") == llm.PROVIDER_BEDROCK
    assert llm.provider_for("oa_report_section") == llm.PROVIDER_BEDROCK


def test_bulk_tasks_route_to_fireworks_once_the_contract_gate_is_open() -> None:
    settings.llm_fireworks_enabled = True
    assert llm.provider_for("classify_intent") == llm.PROVIDER_FIREWORKS
    assert llm.provider_for("triage") == llm.PROVIDER_FIREWORKS


def test_gate_closed_sends_bulk_work_to_bedrock_instead() -> None:
    """D45 owner gate: until no-training/ZDR is in the signed contract, bulk work costs more
    rather than going somewhere unvetted. Forgetting to open the gate must never be the unsafe
    failure."""
    settings.llm_fireworks_enabled = False
    assert llm.provider_for("classify_intent") == llm.PROVIDER_BEDROCK


def test_gate_never_diverts_sensitive_work() -> None:
    settings.llm_fireworks_enabled = True
    assert llm.provider_for("draft_reply") == llm.PROVIDER_BEDROCK


def test_unknown_task_defaults_to_the_careful_provider() -> None:
    """An unclassified task is one nobody has thought about yet."""
    assert llm.sensitivity_of("some_new_task") == llm.SENSITIVE
    assert llm.provider_for("some_new_task") == llm.PROVIDER_BEDROCK


def test_routing_is_a_property_of_the_task_not_the_call_site() -> None:
    """`get_llm_client` takes only a task name — there is no parameter a caller could use to
    push client-facing prose onto the cheap provider."""
    settings.llm_fireworks_enabled = True
    sensitive = llm.get_llm_client("draft_reply")
    bulk = llm.get_llm_client("triage")
    assert sensitive.provider == llm.PROVIDER_BEDROCK
    assert bulk.provider == llm.PROVIDER_FIREWORKS


def test_bedrock_model_is_the_us_profile_and_says_so() -> None:
    """Doc-drift fix: the legacy docstring claimed ca-central-1 while production ran the `us.`
    cross-region profile. The code states the real posture."""
    client = llm.get_llm_client("draft_reply")
    assert client.model.startswith("us.")
