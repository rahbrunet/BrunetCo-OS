"""Task-Rule Builder validation, test-case generation, round-trip (WP 6.6, §A2) — pure, no DB.

A docket rule is the highest-blast-radius configuration in the system: a wrong one silently
mis-dates every matter it touches. These tests pin the checks that run before a human is asked to
approve one.
"""
from __future__ import annotations

from datetime import date

import pytest
from py_shared.domain import rule_builder as rb

GOOD = {
    "title": "Respond to Office Action",
    "deadline_type": "extendable_external",
    "offsets": {"respond_by": {"months": 4}, "final_due_date": {"months": 6}},
    "business_day_roll": True,
}


# --- validation ----------------------------------------------------------------


def test_a_sound_definition_validates() -> None:
    rb.validate_definition(GOOD)


def test_a_definition_needs_a_title() -> None:
    with pytest.raises(rb.RuleDraftError, match="title"):
        rb.validate_definition({**GOOD, "title": ""})


def test_an_unknown_deadline_type_is_refused() -> None:
    with pytest.raises(rb.RuleDraftError, match="deadline type"):
        rb.validate_definition({**GOOD, "deadline_type": "vibes"})


def test_a_definition_needs_at_least_one_offset() -> None:
    """A rule with no offsets produces no dates — and an empty preview reads as 'nothing to worry
    about'."""
    with pytest.raises(rb.RuleDraftError, match="at least one offset"):
        rb.validate_definition({**GOOD, "offsets": {}})


def test_an_unknown_offset_key_is_refused() -> None:
    with pytest.raises(rb.RuleDraftError, match="unknown offset"):
        rb.validate_definition({**GOOD, "offsets": {"whenever": {"months": 1}}})


def test_an_unknown_unit_is_refused() -> None:
    with pytest.raises(rb.RuleDraftError, match="unknown unit"):
        rb.validate_definition({**GOOD, "offsets": {"respond_by": {"fortnights": 2}}})


def test_a_negative_offset_is_refused() -> None:
    with pytest.raises(rb.RuleDraftError, match="non-negative"):
        rb.validate_definition({**GOOD, "offsets": {"respond_by": {"months": -1}}})


def test_a_boolean_offset_amount_is_refused() -> None:
    """bool subclasses int; True months would compute as 1 month and look deliberate."""
    with pytest.raises(rb.RuleDraftError, match="non-negative"):
        rb.validate_definition({**GOOD, "offsets": {"respond_by": {"months": True}}})


def test_respond_by_after_final_due_is_refused() -> None:
    """The engine would compute this happily and docket backwards, which is why it is caught."""
    inverted = {**GOOD, "offsets": {"respond_by": {"months": 8},
                                    "final_due_date": {"months": 6}}}
    with pytest.raises(rb.RuleDraftError, match="falls after"):
        rb.validate_definition(inverted)


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(rb.RuleDraftError) as exc:
        rb.validate_definition({"title": "", "deadline_type": "nope", "offsets": {}})
    assert str(exc.value).count(";") >= 2


# --- test-case generation (M1-R4) ----------------------------------------------


def test_cases_use_the_real_deadline_engine() -> None:
    cases = rb.generate_test_cases(GOOD, [date(2026, 1, 15)])
    assert cases[0].dates["respond_by"] == date(2026, 5, 15)
    assert cases[0].dates["final_due_date"] == date(2026, 7, 15)


def test_month_end_clamping_shows_up_in_the_cases() -> None:
    """31 Jan + 1 month clamps to 28 Feb — the case a practitioner most needs to see.

    With business-day rolling off, the clamp is visible on its own.
    """
    rule = {**GOOD, "offsets": {"respond_by": {"months": 1}}, "business_day_roll": False}
    cases = rb.generate_test_cases(rule, [date(2026, 1, 31)])
    assert cases[0].dates["respond_by"] == date(2026, 2, 28)


def test_clamping_and_rolling_compose_and_the_trace_explains_it() -> None:
    """31 Jan 2026 + 1 month clamps to Sat 28 Feb, which then rolls to Mon 2 Mar. A date two days
    past the "obvious" answer is exactly what makes a practitioner distrust the docket — so the
    trace has to spell out both steps."""
    rule = {**GOOD, "offsets": {"respond_by": {"months": 1}}}
    cases = rb.generate_test_cases(rule, [date(2026, 1, 31)])
    assert cases[0].dates["respond_by"] == date(2026, 3, 2)
    assert cases[0].rolled["respond_by"]


def test_a_holiday_roll_is_explained_in_the_trace() -> None:
    """A surprising date must explain itself rather than looking like a bug."""
    rule = {**GOOD, "offsets": {"respond_by": {"days": 1}}}
    # 2026-07-03 is a Friday; +1 day lands Saturday and rolls to Monday.
    cases = rb.generate_test_cases(rule, [date(2026, 7, 3)])
    assert cases[0].dates["respond_by"] == date(2026, 7, 6)
    assert cases[0].rolled["respond_by"]


def test_generation_validates_first() -> None:
    with pytest.raises(rb.RuleDraftError):
        rb.generate_test_cases({**GOOD, "offsets": {}}, [date(2026, 1, 1)])


def test_default_trigger_dates_cover_the_awkward_cases() -> None:
    dates = rb.default_trigger_dates(date(2026, 7, 20))
    assert date(2026, 1, 31) in dates      # month-end clamping
    assert date(2026, 12, 31) in dates     # year boundary


# --- parsing the model's draft -------------------------------------------------


def test_a_well_formed_draft_parses() -> None:
    raw = '{"trigger_code": "office_action", "definition": ' + str(GOOD).replace("'", '"') \
        .replace("True", "true") + "}"
    trigger, definition = rb._parse_draft(raw)
    assert trigger == "office_action"
    assert definition["deadline_type"] == "extendable_external"


def test_no_json_is_a_clean_error() -> None:
    with pytest.raises(rb.RuleDraftError, match="no JSON"):
        rb._parse_draft("I could not build that rule.")


def test_a_draft_missing_the_definition_is_refused() -> None:
    with pytest.raises(rb.RuleDraftError, match="missing"):
        rb._parse_draft('{"trigger_code": "office_action"}')
