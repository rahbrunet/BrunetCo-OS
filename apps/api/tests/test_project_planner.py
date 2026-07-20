"""NL project-plan parsing (WP 5.6, §M9) — pure, no DB.

An LLM is a best-effort source: it wraps JSON in prose, omits fields, mistypes numbers. Each is a
usable PlanningError, not a stack trace, because this text is shown to the user who typed the
description.
"""
from __future__ import annotations

import pytest
from py_shared.domain import project_planner as pp

GOOD = """Here is your plan:
{"tasks": [
  {"ref": "draft", "title": "Draft assignment", "role": "agent", "cycle_days": 3, "stage": "Prep"},
  {"ref": "review", "title": "Review", "role": "principal", "cycle_days": 1}
], "edges": [{"task": "review", "depends_on": "draft"}]}
Hope that helps!"""


def test_a_well_formed_plan_parses() -> None:
    tasks, edges = pp._parse_plan(GOOD)
    assert [t.task_ref for t in tasks] == ["draft", "review"]
    assert tasks[0].cycle_days == 3
    assert tasks[0].stage == "Prep"
    assert edges == [pp.TemplateEdge("review", "draft")]


def test_json_is_extracted_from_surrounding_prose() -> None:
    """The model's chatty preamble/postamble must not defeat parsing."""
    tasks, _ = pp._parse_plan(GOOD)
    assert tasks


def test_no_json_is_a_clean_error() -> None:
    with pytest.raises(pp.PlanningError, match="no JSON"):
        pp._parse_plan("I couldn't produce a plan for that.")


def test_invalid_json_is_a_clean_error() -> None:
    with pytest.raises(pp.PlanningError, match="valid JSON"):
        pp._parse_plan('{"tasks": [ broken ]}')


def test_a_plan_with_no_tasks_is_refused() -> None:
    with pytest.raises(pp.PlanningError, match="no tasks"):
        pp._parse_plan('{"tasks": []}')


def test_a_task_missing_its_title_is_refused() -> None:
    with pytest.raises(pp.PlanningError, match="malformed task"):
        pp._parse_plan('{"tasks": [{"ref": "x"}]}')


def test_a_non_integer_cycle_is_refused() -> None:
    with pytest.raises(pp.PlanningError, match="cycle_days"):
        pp._parse_plan('{"tasks": [{"ref": "x", "title": "X", "cycle_days": "soon"}]}')


def test_a_negative_cycle_is_clamped_to_zero() -> None:
    """A milestone-like zero is fine; a negative is nonsense the parser normalises rather than
    passing to the scheduler."""
    tasks, _ = pp._parse_plan('{"tasks": [{"ref": "x", "title": "X", "cycle_days": -5}]}')
    assert tasks[0].cycle_days == 0


def test_edges_are_optional() -> None:
    tasks, edges = pp._parse_plan('{"tasks": [{"ref": "x", "title": "X"}]}')
    assert edges == []


def test_a_malformed_edge_is_refused() -> None:
    with pytest.raises(pp.PlanningError, match="malformed edge"):
        pp._parse_plan(
            '{"tasks": [{"ref": "a", "title": "A"}], "edges": [{"task": "a"}]}'
        )


def test_rehydration_restores_identities_in_titles() -> None:
    tasks = [pp.TemplateTask(task_ref="t", title="Recordal for ORG_001", stage="ORG_001 stage")]
    restored = pp._rehydrate_plan(tasks, {"ORG_001": "Acme Corp"})
    assert restored[0].title == "Recordal for Acme Corp"
    assert restored[0].stage == "Acme Corp stage"
