"""Board typed-value validation + automation matching (WP 5.3, §M9) — pure, no DB.

The trigger matcher is the contract the whole no-code automation layer rests on. A rule that
fires when it shouldn't creates phantom work; one that stays silent when it should leaves the
firm's process un-automated. Both are pinned here.
"""
from __future__ import annotations

import pytest
from py_shared.domain import boards

# --- typed value validation ----------------------------------------------------


def test_none_is_always_valid() -> None:
    """Clearing a cell is a legitimate state, not a type error."""
    for col_type in (boards.TEXT, boards.NUMBER, boards.DATE, boards.PERSON, boards.CHECKBOX):
        boards.validate_field_value(col_type, None)


@pytest.mark.parametrize(
    "col_type,value",
    [
        (boards.TEXT, "hello"),
        (boards.NUMBER, 42),
        (boards.NUMBER, 3.14),
        (boards.DATE, "2026-07-19"),
        (boards.PERSON, "11111111-1111-1111-1111-111111111111"),
        (boards.CHECKBOX, True),
    ],
)
def test_well_typed_values_pass(col_type: str, value: object) -> None:
    boards.validate_field_value(col_type, value)


@pytest.mark.parametrize(
    "col_type,value",
    [
        (boards.TEXT, 5),
        (boards.NUMBER, "lots"),
        (boards.DATE, "next Tuesday"),
        (boards.PERSON, "not-a-uuid"),
        (boards.CHECKBOX, "yes"),
    ],
)
def test_mistyped_values_are_refused(col_type: str, value: object) -> None:
    with pytest.raises(boards.FieldValueInvalid):
        boards.validate_field_value(col_type, value)


def test_a_boolean_is_not_a_number() -> None:
    """bool subclasses int in Python; a checkbox value landing in a number column is a real bug
    that a naive isinstance(int) check would wave through."""
    with pytest.raises(boards.FieldValueInvalid):
        boards.validate_field_value(boards.NUMBER, True)


def test_select_value_must_be_an_allowed_option() -> None:
    config = {"options": [{"value": "high"}, {"value": "low"}]}
    boards.validate_field_value(boards.SINGLE_SELECT, "high", config)
    with pytest.raises(boards.FieldValueInvalid):
        boards.validate_field_value(boards.SINGLE_SELECT, "medium", config)


# --- automation validation -----------------------------------------------------


def test_a_valid_automation_passes() -> None:
    boards.validate_automation(
        {"event": "status_changed", "to": "done"},
        [{"type": "create_task", "title": "Invoice review", "role": "bookkeeper"}],
    )


def test_unknown_trigger_event_is_refused() -> None:
    with pytest.raises(boards.AutomationInvalid, match="trigger event"):
        boards.validate_automation({"event": "moon_phase"}, [{"type": "notify", "target": "owner"}])


def test_field_changed_trigger_needs_a_column() -> None:
    with pytest.raises(boards.AutomationInvalid, match="column"):
        boards.validate_automation(
            {"event": "field_changed"}, [{"type": "notify", "target": "owner"}]
        )


def test_an_automation_with_no_actions_is_refused() -> None:
    with pytest.raises(boards.AutomationInvalid, match="no actions"):
        boards.validate_automation({"event": "status_changed"}, [])


def test_create_task_needs_a_title() -> None:
    with pytest.raises(boards.AutomationInvalid, match="title"):
        boards.validate_automation({"event": "status_changed"}, [{"type": "create_task"}])


def test_notify_needs_a_valid_target() -> None:
    with pytest.raises(boards.AutomationInvalid, match="target"):
        boards.validate_automation(
            {"event": "status_changed"}, [{"type": "notify", "target": "everyone"}]
        )


# --- trigger matching ----------------------------------------------------------


def test_status_trigger_matches_the_target_status() -> None:
    trigger = {"event": "status_changed", "to": "done"}
    assert boards.match_trigger(trigger, {"type": "status_changed", "to": "done"})
    assert not boards.match_trigger(trigger, {"type": "status_changed", "to": "in_progress"})


def test_a_status_trigger_with_no_target_matches_any_status_change() -> None:
    trigger = {"event": "status_changed"}
    assert boards.match_trigger(trigger, {"type": "status_changed", "to": "anything"})


def test_a_status_trigger_ignores_field_events() -> None:
    trigger = {"event": "status_changed", "to": "done"}
    assert not boards.match_trigger(trigger, {"type": "field_changed", "column": "x", "to": "done"})


def test_field_trigger_matches_only_its_own_column() -> None:
    trigger = {"event": "field_changed", "column": "priority", "to": "high"}
    assert boards.match_trigger(
        trigger, {"type": "field_changed", "column": "priority", "to": "high"}
    )
    assert not boards.match_trigger(
        trigger, {"type": "field_changed", "column": "estimate", "to": "high"}
    )


def test_field_trigger_with_no_target_matches_any_value_on_its_column() -> None:
    trigger = {"event": "field_changed", "column": "priority"}
    assert boards.match_trigger(
        trigger, {"type": "field_changed", "column": "priority", "to": "whatever"}
    )
