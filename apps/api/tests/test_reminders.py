"""A18 ladder engine — pure logic, no DB (WP 6.12, D31).

The maintenance-fee ladder from the spec (T−60 courtesy, T−30 action requested, T−14 FINAL
REMINDER) is used as the worked example throughout, because if the engine gets that one wrong a
client loses a patent.
"""
from __future__ import annotations

from datetime import date

import pytest
from py_shared.domain.reminders import (
    LadderConfigError,
    Rung,
    due_rungs,
    is_exhausted,
    render_template,
    resolve_send_mode,
    rung_due_date,
    validate_rungs,
)

DEADLINE = date(2026, 12, 1)

MAINTENANCE = [
    Rung(1, -60, "courtesy", "{matter_reference}: maintenance fee due {due_date}", "Courtesy."),
    Rung(2, -30, "action requested", "{matter_reference}: instructions needed", "Please instruct."),
    Rung(3, -14, "FINAL REMINDER", "FINAL REMINDER — {matter_reference}", "Will be abandoned."),
]


# --- template rendering --------------------------------------------------------


def test_rendering_fills_every_placeholder() -> None:
    out = render_template(
        "{matter_reference} due {due_date}",
        {"matter_reference": "1234CA", "due_date": "2026-12-01"},
    )
    assert out == "1234CA due 2026-12-01"


def test_an_unfillable_placeholder_refuses_to_render() -> None:
    """Better a loud failure than a client receiving 'your {jurisdiction} deadline'."""
    with pytest.raises(LadderConfigError, match="jurisdiction"):
        render_template("your {jurisdiction} deadline", {"matter_reference": "1234CA"})


# --- ladder shape validation ---------------------------------------------------


def test_the_maintenance_ladder_validates() -> None:
    validate_rungs("deadline", MAINTENANCE)


def test_a_deadline_ladder_may_not_chase_after_the_deadline() -> None:
    with pytest.raises(LadderConfigError, match="negative offset"):
        validate_rungs("deadline", [Rung(1, 7, "late", "s", "b")])


def test_a_follow_up_ladder_may_not_chase_before_the_tag() -> None:
    with pytest.raises(LadderConfigError, match="positive offset"):
        validate_rungs("awaiting_client", [Rung(1, -7, "early", "s", "b")])


def test_rung_offsets_must_escalate() -> None:
    """Later rung = closer to the deadline. The catch-up rule depends on this being true."""
    out_of_order = [Rung(1, -14, "a", "s", "b"), Rung(2, -60, "b", "s", "b")]
    with pytest.raises(LadderConfigError, match="strictly increase"):
        validate_rungs("deadline", out_of_order)


def test_an_empty_ladder_is_refused() -> None:
    with pytest.raises(LadderConfigError, match="at least one rung"):
        validate_rungs("deadline", [])


# --- due-date arithmetic and rung selection ------------------------------------


def test_rung_dates_count_back_from_the_deadline() -> None:
    assert rung_due_date(DEADLINE, MAINTENANCE[0]) == date(2026, 10, 2)   # T−60
    assert rung_due_date(DEADLINE, MAINTENANCE[2]) == date(2026, 11, 17)  # T−14


def test_nothing_is_due_before_the_first_rung() -> None:
    to_send, superseded = due_rungs(DEADLINE, MAINTENANCE, date(2026, 9, 1), set())
    assert to_send is None and superseded == []


def test_one_rung_comes_due_at_a_time_on_a_normal_sweep() -> None:
    to_send, superseded = due_rungs(DEADLINE, MAINTENANCE, date(2026, 10, 2), set())
    assert to_send is not None and to_send.step_no == 1
    assert superseded == []


def test_an_already_sent_rung_is_not_resent() -> None:
    to_send, _ = due_rungs(DEADLINE, MAINTENANCE, date(2026, 10, 2), {1})
    assert to_send is None


def test_a_missed_sweep_sends_the_most_escalated_rung_not_all_of_them() -> None:
    """A sweep outage must never burst three escalating emails at a client in one minute — and it
    must never cost them the FINAL REMINDER either."""
    to_send, superseded = due_rungs(DEADLINE, MAINTENANCE, date(2026, 11, 20), set())
    assert to_send is not None and to_send.label == "FINAL REMINDER"
    assert [r.step_no for r in superseded] == [1, 2]


# --- exhaustion ----------------------------------------------------------------


def test_a_ladder_with_rungs_left_is_not_exhausted() -> None:
    assert not is_exhausted(DEADLINE, MAINTENANCE, date(2026, 11, 20), {1, 2})


def test_a_ladder_is_exhausted_once_the_last_rung_has_passed() -> None:
    assert is_exhausted(DEADLINE, MAINTENANCE, date(2026, 11, 17), {1, 2, 3})


def test_an_empty_ladder_never_reports_exhaustion() -> None:
    assert not is_exhausted(DEADLINE, [], date(2026, 12, 1), set())


# --- send mode (D31, as revised) -----------------------------------------------


def test_v1_default_is_review_first() -> None:
    """auto_remind defaults off, so every reminder goes through the audit queue."""
    assert resolve_send_mode(auto_remind=False, unsubscribed=False, ai_composed=False) == "review"


def test_ai_composed_content_is_reviewed_even_with_auto_remind_on() -> None:
    assert resolve_send_mode(auto_remind=True, unsubscribed=False, ai_composed=True) == "review"


def test_unsubscribe_beats_auto_remind() -> None:
    assert resolve_send_mode(auto_remind=True, unsubscribed=True, ai_composed=False) == "suppress"


def test_unsubscribe_beats_review_too() -> None:
    assert resolve_send_mode(auto_remind=False, unsubscribed=True, ai_composed=False) == "suppress"


def test_the_dormant_auto_path_is_reachable_only_by_deterministic_content() -> None:
    assert resolve_send_mode(auto_remind=True, unsubscribed=False, ai_composed=False) == "auto"
