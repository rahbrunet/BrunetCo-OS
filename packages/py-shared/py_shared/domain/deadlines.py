"""Deadline calculation — the pure date math of the docketing engine (M1-R2, WP 1.2).

No I/O here: callers supply the holiday set. Everything is exhaustively golden-testable.

* `add_offset` — calendar arithmetic with month-end clamping (Jan 31 + 1 month = Feb 28/29),
  the convention office deadline rules use.
* `roll_forward` — weekend/holiday roll to the next business day, returning the full trace of
  every step taken. The trace is stored verbatim in the M1-R14 provenance record so a calculated
  date is always explainable ("raw date was a Sunday → rolled to Monday → Monday was Victoria
  Day → rolled to Tuesday").
* `compute_deadlines` — applies a rule's declarative offsets to a trigger date and returns the
  dual dates (M1-R11: RespondBy + FinalDueDate) plus traces, ready for the provenance log.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

_WEEKEND = (5, 6)  # Saturday, Sunday


@dataclass
class RollStep:
    """One step of a weekend/holiday roll."""

    from_date: date
    to_date: date
    reason: str  # 'weekend' or the holiday name

    def as_json(self) -> dict[str, str]:
        return {
            "from": self.from_date.isoformat(),
            "to": self.to_date.isoformat(),
            "reason": self.reason,
        }


@dataclass
class CalculatedDate:
    """A computed deadline: the raw offset result, the rolled date, and how it got there."""

    raw: date
    rolled: date
    trace: list[RollStep] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "raw": self.raw.isoformat(),
            "rolled": self.rolled.isoformat(),
            "trace": [s.as_json() for s in self.trace],
        }


def add_offset(start: date, years: int = 0, months: int = 0, days: int = 0) -> date:
    """Calendar offset with month-end clamping.

    Years/months first (clamping the day to the target month's length), then days —
    the ordering office rules use: "30 months from the priority date" means calendar months.
    """
    total_months = start.month - 1 + months + years * 12
    year = start.year + total_months // 12
    month = total_months % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) + timedelta(days=days)


def roll_forward(when: date, holidays: dict[date, str]) -> tuple[date, list[RollStep]]:
    """Roll a date forward past weekends and holidays to the next business day.

    ``holidays`` maps date → holiday name for the applicable jurisdiction calendar.
    Returns the final date and the step-by-step trace (empty if no roll was needed).
    """
    steps: list[RollStep] = []
    current = when
    while True:
        if current.weekday() in _WEEKEND:
            reason = "weekend"
        elif current in holidays:
            reason = holidays[current]
        else:
            return current, steps
        nxt = current + timedelta(days=1)
        steps.append(RollStep(from_date=current, to_date=nxt, reason=reason))
        current = nxt


def compute_deadlines(
    definition: dict[str, Any], ref_date: date, holidays: dict[date, str]
) -> dict[str, CalculatedDate]:
    """Apply a rule's declarative ``offsets`` to a trigger date (M1-R11 dual dates).

    ``definition`` is the docket_rules.definition jsonb:
      offsets: {"respond_by": {years, months, days}, "final_due_date": {…}} — either key optional
      business_day_roll: bool (default true)
    Returns a CalculatedDate per offset key present, traces included.
    """
    offsets: dict[str, dict[str, int]] = definition.get("offsets", {})
    do_roll: bool = definition.get("business_day_roll", True)
    out: dict[str, CalculatedDate] = {}
    for key, off in offsets.items():
        raw = add_offset(
            ref_date,
            years=int(off.get("years", 0)),
            months=int(off.get("months", 0)),
            days=int(off.get("days", 0)),
        )
        if do_roll:
            rolled, trace = roll_forward(raw, holidays)
        else:
            rolled, trace = raw, []
        out[key] = CalculatedDate(raw=raw, rolled=rolled, trace=trace)
    return out
