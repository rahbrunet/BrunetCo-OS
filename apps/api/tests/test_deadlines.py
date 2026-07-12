"""Golden tests for the pure deadline math (M1-R2, WP 1.2) — no DB.

These are the reference cases the engine must never drift from: calendar-month offsets with
month-end clamping, leap years, weekend and holiday rolls (with full traces), and the CIPO
patterns called out in the spec (maintenance fees run from the 2nd anniversary of filing).
"""
from __future__ import annotations

from datetime import date

from py_shared.domain.deadlines import add_offset, compute_deadlines, roll_forward

# --- add_offset: calendar arithmetic -----------------------------------------

def test_simple_month_offset() -> None:
    assert add_offset(date(2026, 1, 15), months=3) == date(2026, 4, 15)


def test_year_offset_crosses_years() -> None:
    assert add_offset(date(2026, 11, 30), months=3) == date(2027, 2, 28)


def test_month_end_clamps_to_short_month() -> None:
    assert add_offset(date(2026, 1, 31), months=1) == date(2026, 2, 28)
    assert add_offset(date(2026, 3, 31), months=1) == date(2026, 4, 30)


def test_leap_year_clamp() -> None:
    assert add_offset(date(2024, 1, 31), months=1) == date(2024, 2, 29)   # leap
    assert add_offset(date(2023, 2, 28), years=1) == date(2024, 2, 28)
    assert add_offset(date(2024, 2, 29), years=1) == date(2025, 2, 28)    # clamped


def test_days_applied_after_months() -> None:
    # months first (with clamp), then days — 1 month + 15 days from Jan 31.
    assert add_offset(date(2026, 1, 31), months=1, days=15) == date(2026, 3, 15)


def test_cipo_maintenance_from_second_anniversary() -> None:
    # CIPO maintenance fees run from the 2nd anniversary of filing (M1-R2).
    assert add_offset(date(2023, 3, 15), years=2) == date(2025, 3, 15)


def test_pct_thirty_month_national_phase() -> None:
    # 30 months from priority — the classic PCT national-phase window.
    assert add_offset(date(2024, 5, 10), months=30) == date(2026, 11, 10)


# --- roll_forward: weekends + holidays ---------------------------------------

def test_business_day_needs_no_roll() -> None:
    rolled, trace = roll_forward(date(2026, 7, 15), {})  # a Wednesday
    assert rolled == date(2026, 7, 15) and trace == []


def test_saturday_rolls_to_monday() -> None:
    rolled, trace = roll_forward(date(2026, 7, 18), {})  # Saturday
    assert rolled == date(2026, 7, 20)
    assert [s.reason for s in trace] == ["weekend", "weekend"]


def test_holiday_rolls_to_next_day() -> None:
    holidays = {date(2026, 7, 1): "Canada Day"}  # a Wednesday
    rolled, trace = roll_forward(date(2026, 7, 1), holidays)
    assert rolled == date(2026, 7, 2)
    assert trace[0].reason == "Canada Day"


def test_weekend_then_holiday_chain_roll() -> None:
    # Friday 2026-12-25 (Christmas) → Sat → Sun → Mon 2026-12-28 (Boxing Day observed) → Tue.
    holidays = {date(2026, 12, 25): "Christmas Day", date(2026, 12, 28): "Boxing Day (observed)"}
    rolled, trace = roll_forward(date(2026, 12, 25), holidays)
    assert rolled == date(2026, 12, 29)
    assert [s.reason for s in trace] == [
        "Christmas Day", "weekend", "weekend", "Boxing Day (observed)",
    ]
    # Trace is contiguous: each step starts where the previous ended.
    for prev, nxt in zip(trace, trace[1:], strict=False):
        assert prev.to_date == nxt.from_date


# --- compute_deadlines: dual dates from a declarative rule -------------------

def test_dual_dates_with_roll_and_trace() -> None:
    definition = {
        "title": "Respond to Office Action",
        "deadline_type": "extendable_external",
        # 2026-01-17 + 6m = Fri 2026-07-17 (no roll); + 10m = Tue 2026-11-17 (no roll).
        "offsets": {"respond_by": {"months": 6}, "final_due_date": {"months": 10}},
    }
    out = compute_deadlines(definition, date(2026, 1, 17), {})
    assert out["respond_by"].rolled == date(2026, 7, 17)
    assert out["final_due_date"].rolled == date(2026, 11, 17)


def test_rolled_deadline_keeps_raw_date_for_provenance() -> None:
    definition = {
        "title": "Pay maintenance fee",
        "deadline_type": "hard_external",
        "offsets": {"final_due_date": {"years": 2}},
    }
    # 2024-07-13 + 2y = Mon 2026-07-13? No: 2026-07-13 IS a Monday. Use a weekend case:
    # 2024-07-18 + 2y = Sat 2026-07-18 → rolls to Mon 2026-07-20.
    out = compute_deadlines(definition, date(2024, 7, 18), {})
    calc = out["final_due_date"]
    assert calc.raw == date(2026, 7, 18)
    assert calc.rolled == date(2026, 7, 20)
    json_form = calc.as_json()
    assert json_form["raw"] == "2026-07-18"
    assert json_form["rolled"] == "2026-07-20"
    assert len(json_form["trace"]) == 2


def test_business_day_roll_can_be_disabled() -> None:
    definition = {
        "title": "Anniversary event",
        "deadline_type": "event",
        "offsets": {"final_due_date": {"years": 1}},
        "business_day_roll": False,
    }
    out = compute_deadlines(definition, date(2025, 7, 19), {})  # lands Sun 2026-07-19
    assert out["final_due_date"].rolled == date(2026, 7, 19)
    assert out["final_due_date"].trace == []
