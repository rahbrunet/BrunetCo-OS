"""Time entry, flat-fee attribution and collected-allocation arithmetic
(WPs 2.5.1–2.5.3, spec §M2, D42 M2-R10/R11/R13).

Everything in this module is pure and integer-exact, deliberately. Xero's payment webhooks are
invoice-level, so line-level `collected` attribution is arithmetic the OS performs on its own —
and that arithmetic decides what each timekeeper is paid (D42 bonus accrual). It therefore gets
built and tested independently of the webhook that will eventually drive it, rather than being
written in a hurry against a live integration in Phase 3.

Units, restated because every function here depends on them:
  * money  -> integer **cents**
  * time   -> integer **minutes**
  * shares -> integer **basis points** (10000 bps = 100%)

No floats anywhere in a value that reaches a person's pay.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BPS_TOTAL = 10_000


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d+):([0-5]\d)$"), "hh:mm"),
    (re.compile(r"^(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?$", re.I), "hours"),
    (re.compile(r"^(\d+)\s*m(?:in(?:ute)?s?)?$", re.I), "minutes"),
    (re.compile(r"^(\d+(?:\.\d+)?)$"), "hours"),  # bare number = hours, the docketing convention
)


class DurationError(ValueError):
    """The duration string could not be parsed. Never guessed — a mis-parsed duration bills the
    client the wrong amount and pays the timekeeper the wrong bonus."""


def parse_duration(text: str) -> int:
    """Parse a human-entered duration into whole minutes.

    Accepts "1:30", "1.5h", "90m", "1.5". Fractional hours are rounded to the nearest minute
    (0.1h = 6 min exactly; 0.7h = 42 min). Rounding to the nearest minute rather than truncating
    matters at scale: truncation is systematically biased against the timekeeper.
    """
    raw = text.strip()
    if not raw:
        raise DurationError("empty duration")
    for pattern, kind in _DURATION_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue
        if kind == "hh:mm":
            return int(match.group(1)) * 60 + int(match.group(2))
        if kind == "hours":
            return round(float(match.group(1)) * 60)
        return int(match.group(1))
    raise DurationError(f"unrecognized duration {text!r}")


def entry_amount_cents(minutes: int, rate_cents: int) -> int:
    """Value of a time entry: minutes at an hourly rate, rounded to the nearest cent.

    Integer arithmetic before the divide, so the rounding happens once and is not the accumulated
    residue of a float multiplication.
    """
    if minutes < 0 or rate_cents < 0:
        raise ValueError("minutes and rate must be non-negative")
    return (minutes * rate_cents + 30) // 60


# ---------------------------------------------------------------------------
# Flat-fee attribution (M2-R13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    timekeeper_id: str
    share_bps: int


def validate_splits(splits: list[Attribution]) -> None:
    """Splits must total exactly 100%. Empty means "100% to the performer" and is valid.

    The DB enforces this too (migration 0016). Both, because the constraint catches whatever
    reaches the database and this catches it early enough to show the user a sensible message.
    """
    if not splits:
        return
    total = sum(s.share_bps for s in splits)
    if total != BPS_TOTAL:
        raise ValueError(f"attribution must total {BPS_TOTAL} bps (100%), got {total}")
    if len({s.timekeeper_id for s in splits}) != len(splits):
        raise ValueError("duplicate timekeeper in attribution split")


def split_amount(amount_cents: int, splits: list[Attribution]) -> dict[str, int]:
    """Divide an amount across timekeepers by basis points, summing to the original **exactly**.

    Uses largest-remainder: floor every share, then hand the leftover cents to the largest
    fractional remainders. Naive per-share rounding loses or invents cents — on a $2,000 fee
    split three ways it is off by one, and "the bonus ledger doesn't tie to the invoice by a
    penny" is a question that costs an afternoon every time it is asked.
    """
    validate_splits(splits)
    if not splits:
        return {}

    exact = [(s.timekeeper_id, amount_cents * s.share_bps) for s in splits]
    floors = {tid: numerator // BPS_TOTAL for tid, numerator in exact}
    remainder = amount_cents - sum(floors.values())

    # Largest fractional part first; timekeeper id breaks ties so the result is deterministic.
    order = sorted(exact, key=lambda item: (-(item[1] % BPS_TOTAL), item[0]))
    for tid, _ in order[:remainder]:
        floors[tid] += 1
    return floors


# ---------------------------------------------------------------------------
# Collected allocation (M2-R11) — the Phase-3 wiring target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceLine:
    line_id: str
    billed_cents: int


@dataclass
class AllocationResult:
    per_line: dict[str, int]
    unallocated_cents: int = 0     # overpayment: money received beyond what was billed
    is_overpayment: bool = False


def allocate_payment(
    lines: list[InvoiceLine],
    payment_cents: int,
    overrides: dict[str, int] | None = None,
) -> AllocationResult:
    """Spread an invoice-level payment across its lines (pro-rata default, override permitted).

    Xero tells the OS "invoice X received $Y"; it does not say which lines. Three cases, each
    with a defined behaviour rather than an exception in production:

      * **Exact / partial payment** — pro-rata by billed amount, largest-remainder so the
        allocations sum to the payment to the cent.
      * **Overpayment** — lines are filled to their billed amounts and the excess is returned as
        `unallocated_cents` for the bookkeeper to place (credit, trust, or refund). Silently
        over-allocating a line would inflate the collected figure and therefore a bonus accrual.
      * **Override** — a bookkeeper naming specific amounts. Honoured as given; the remainder is
        pro-rated across the lines not named.

    Zero-billed lines receive nothing: they cannot take a pro-rata share of anything.
    """
    if payment_cents < 0:
        raise ValueError("payment must be non-negative")

    result: dict[str, int] = {line.line_id: 0 for line in lines}
    overrides = overrides or {}
    unknown = set(overrides) - set(result)
    if unknown:
        raise ValueError(f"override names unknown lines: {sorted(unknown)}")

    remaining = payment_cents
    for line_id, amount in overrides.items():
        if amount < 0:
            raise ValueError("override amounts must be non-negative")
        result[line_id] = amount
        remaining -= amount
    if remaining < 0:
        raise ValueError("overrides exceed the payment amount")

    open_lines = [ln for ln in lines if ln.line_id not in overrides and ln.billed_cents > 0]
    total_billed = sum(ln.billed_cents for ln in open_lines)

    if total_billed == 0:
        return AllocationResult(result, unallocated_cents=remaining,
                                is_overpayment=remaining > 0)

    # Overpayment: cap each line at what it was billed, hand back the excess.
    if remaining > total_billed:
        for ln in open_lines:
            result[ln.line_id] = ln.billed_cents
        return AllocationResult(result, unallocated_cents=remaining - total_billed,
                                is_overpayment=True)

    exact = [(ln.line_id, remaining * ln.billed_cents) for ln in open_lines]
    for line_id, numerator in exact:
        result[line_id] = numerator // total_billed
    leftover = remaining - sum(result[ln.line_id] for ln in open_lines)
    order = sorted(exact, key=lambda item: (-(item[1] % total_billed), item[0]))
    for line_id, _ in order[:leftover]:
        result[line_id] += 1

    return AllocationResult(result)


def apply_write_down(collected: dict[str, int], line_id: str, amount_cents: int) -> dict[str, int]:
    """Reduce a line's collected figure by a reason-coded write-down (M2-R10).

    Returns a new mapping; the caller records the adjustment as a signed row in
    `app.collected_allocations` rather than editing history. The original entry stands — that is
    the whole point of retaining entered *and* billed, and it is what makes a write-down auditable
    instead of merely invisible.
    """
    if line_id not in collected:
        raise ValueError(f"unknown line {line_id!r}")
    if amount_cents < 0:
        raise ValueError("write-down amount must be non-negative")
    updated = dict(collected)
    updated[line_id] = collected[line_id] - amount_cents
    return updated


# ---------------------------------------------------------------------------
# Flat-fee pricing (effective-dated client overrides)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceOverride:
    amount_cents: int
    effective_from: str  # ISO date


def resolve_flat_fee(
    standard_cents: int, overrides: list[PriceOverride], on_date: str,
) -> int:
    """The price in force for a client on a date: the latest override effective on or before that
    date, else the standard price.

    Effective-dated rather than mutable for the reason fees are (WP 2.3): re-pricing a service
    must not silently change what a past matter was quoted or invoiced.
    """
    applicable = [o for o in overrides if o.effective_from <= on_date]
    if not applicable:
        return standard_cents
    return max(applicable, key=lambda o: o.effective_from).amount_cents
