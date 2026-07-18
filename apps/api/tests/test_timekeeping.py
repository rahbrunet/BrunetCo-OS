"""Timekeeper production arithmetic (WPs 2.5.1–2.5.3, D42) — integer-exact, no DB.

These are the calculations that decide what people are paid, so the tests assert exactness
rather than approximate agreement. Every "sums to the cent" assertion below is a payroll dispute
that does not happen.
"""
from __future__ import annotations

import pytest
from py_shared.domain import timekeeping as tk

# --- duration parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("1:30", 90),
        ("0:06", 6),
        ("1.5h", 90),
        ("1.5 hours", 90),
        ("2hr", 120),
        ("90m", 90),
        ("90 min", 90),
        ("1.5", 90),      # bare number = hours, the docketing convention
        ("0.1h", 6),      # the tenth-of-an-hour case decimal hours cannot represent
        ("0.7h", 42),
    ],
)
def test_durations_parse_to_exact_minutes(text: str, minutes: int) -> None:
    assert tk.parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "   ", "later", "1:99", "-1h", "h"])
def test_unparseable_durations_raise_rather_than_guess(text: str) -> None:
    """A guessed duration bills the client wrongly and pays the timekeeper wrongly."""
    with pytest.raises(tk.DurationError):
        tk.parse_duration(text)


def test_fractional_hours_round_rather_than_truncate() -> None:
    """Truncation is systematically biased against the timekeeper, every entry, forever."""
    assert tk.parse_duration("0.99h") == 59


# --- entry value ---------------------------------------------------------------


def test_entry_amount_is_exact_at_the_cent() -> None:
    # 90 minutes at $450.00/h = $675.00
    assert tk.entry_amount_cents(90, 45_000) == 67_500


def test_entry_amount_rounds_half_up_once() -> None:
    # 7 minutes at $400.00/h = $46.666... -> 4667 cents
    assert tk.entry_amount_cents(7, 40_000) == 4_667


# --- flat-fee splits -----------------------------------------------------------


def test_empty_split_means_full_attribution_to_the_performer() -> None:
    tk.validate_splits([])


def test_split_not_totalling_100_percent_is_refused() -> None:
    """9000 bps silently underpays someone and surfaces a quarter later at payroll."""
    with pytest.raises(ValueError, match="10000 bps"):
        tk.validate_splits([tk.Attribution("a", 5000), tk.Attribution("b", 4000)])


def test_duplicate_timekeeper_in_a_split_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        tk.validate_splits([tk.Attribution("a", 5000), tk.Attribution("a", 5000)])


def test_three_way_split_of_an_indivisible_amount_sums_exactly() -> None:
    """$2,000 split three ways: naive rounding loses or invents a cent."""
    splits = [tk.Attribution("a", 3333), tk.Attribution("b", 3333), tk.Attribution("c", 3334)]
    shares = tk.split_amount(200_000, splits)
    assert sum(shares.values()) == 200_000


def test_split_is_deterministic_across_runs() -> None:
    splits = [tk.Attribution("b", 3333), tk.Attribution("a", 3333), tk.Attribution("c", 3334)]
    assert tk.split_amount(100_001, splits) == tk.split_amount(100_001, splits)


def test_sixty_forty_split_is_exact() -> None:
    shares = tk.split_amount(100_000, [tk.Attribution("a", 6000), tk.Attribution("b", 4000)])
    assert shares == {"a": 60_000, "b": 40_000}


# --- collected allocation ------------------------------------------------------


def _lines() -> list[tk.InvoiceLine]:
    return [
        tk.InvoiceLine("l1", 60_000),
        tk.InvoiceLine("l2", 30_000),
        tk.InvoiceLine("l3", 10_000),
    ]


def test_full_payment_allocates_each_line_its_billed_amount() -> None:
    result = tk.allocate_payment(_lines(), 100_000)
    assert result.per_line == {"l1": 60_000, "l2": 30_000, "l3": 10_000}
    assert result.unallocated_cents == 0


def test_partial_payment_is_pro_rata_and_sums_to_the_payment() -> None:
    result = tk.allocate_payment(_lines(), 50_000)
    assert sum(result.per_line.values()) == 50_000
    assert result.per_line["l1"] > result.per_line["l2"] > result.per_line["l3"]


def test_awkward_partial_payment_still_sums_exactly() -> None:
    """The largest-remainder case: 33,333 across 6:3:1 does not divide cleanly."""
    result = tk.allocate_payment(_lines(), 33_333)
    assert sum(result.per_line.values()) == 33_333


def test_overpayment_caps_lines_and_returns_the_excess() -> None:
    """Over-allocating a line would inflate collected — and therefore a bonus accrual."""
    result = tk.allocate_payment(_lines(), 120_000)
    assert result.per_line == {"l1": 60_000, "l2": 30_000, "l3": 10_000}
    assert result.unallocated_cents == 20_000
    assert result.is_overpayment


def test_override_is_honoured_and_the_rest_pro_rated() -> None:
    result = tk.allocate_payment(_lines(), 50_000, overrides={"l3": 10_000})
    assert result.per_line["l3"] == 10_000
    assert sum(result.per_line.values()) == 50_000


def test_override_exceeding_the_payment_is_refused() -> None:
    with pytest.raises(ValueError, match="exceed"):
        tk.allocate_payment(_lines(), 5_000, overrides={"l1": 60_000})


def test_override_naming_an_unknown_line_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown lines"):
        tk.allocate_payment(_lines(), 50_000, overrides={"nope": 100})


def test_zero_billed_lines_receive_nothing() -> None:
    lines = [tk.InvoiceLine("l1", 0), tk.InvoiceLine("l2", 10_000)]
    result = tk.allocate_payment(lines, 5_000)
    assert result.per_line["l1"] == 0
    assert result.per_line["l2"] == 5_000


def test_payment_against_a_fully_zero_invoice_is_all_unallocated() -> None:
    result = tk.allocate_payment([tk.InvoiceLine("l1", 0)], 5_000)
    assert result.unallocated_cents == 5_000
    assert result.is_overpayment


def test_zero_payment_allocates_nothing() -> None:
    result = tk.allocate_payment(_lines(), 0)
    assert set(result.per_line.values()) == {0}


# --- write-down ----------------------------------------------------------------


def test_write_down_reduces_collected_without_touching_the_others() -> None:
    collected = tk.allocate_payment(_lines(), 100_000).per_line
    after = tk.apply_write_down(collected, "l2", 5_000)
    assert after["l2"] == 25_000
    assert after["l1"] == collected["l1"]


def test_write_down_returns_a_new_mapping_leaving_history_intact() -> None:
    """The original stands; the adjustment is a signed row, not an edit (M2-R10)."""
    collected = {"l1": 10_000}
    after = tk.apply_write_down(collected, "l1", 4_000)
    assert collected == {"l1": 10_000}
    assert after == {"l1": 6_000}


# --- flat-fee pricing ----------------------------------------------------------


def test_standard_price_applies_with_no_override() -> None:
    assert tk.resolve_flat_fee(50_000, [], "2026-07-18") == 50_000


def test_client_override_supersedes_the_standard_price() -> None:
    overrides = [tk.PriceOverride(40_000, "2026-01-01")]
    assert tk.resolve_flat_fee(50_000, overrides, "2026-07-18") == 40_000


def test_a_future_override_does_not_apply_yet() -> None:
    overrides = [tk.PriceOverride(40_000, "2027-01-01")]
    assert tk.resolve_flat_fee(50_000, overrides, "2026-07-18") == 50_000


def test_pricing_a_past_date_uses_the_rate_then_in_force() -> None:
    """Re-pricing a service must not change what a past matter was invoiced."""
    overrides = [tk.PriceOverride(40_000, "2025-01-01"), tk.PriceOverride(45_000, "2026-06-01")]
    assert tk.resolve_flat_fee(50_000, overrides, "2025-06-01") == 40_000
    assert tk.resolve_flat_fee(50_000, overrides, "2026-07-18") == 45_000
