"""EOS RAG + To-Do target logic (WP 5.7, §M9) — pure, no DB."""
from __future__ import annotations

import pytest
from py_shared.domain import eos

# --- scorecard RAG -------------------------------------------------------------


def test_no_data_when_value_is_missing() -> None:
    assert eos.rag_status(None, 100, eos.HIGHER_IS_BETTER) == "no_data"


@pytest.mark.parametrize(
    "value,expected",
    [(120, "green"), (100, "green"), (95, "yellow"), (80, "red")],
)
def test_higher_is_better(value: float, expected: str) -> None:
    # goal 100, 10% yellow band → green >=100, yellow >=90, red below.
    assert eos.rag_status(value, 100, eos.HIGHER_IS_BETTER, 0.1) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(3, "green"), (5, "green"), (5.4, "yellow"), (8, "red")],
)
def test_lower_is_better(value: float, expected: str) -> None:
    # goal 5 overdue items, 10% band → green <=5, yellow <=5.5, red above.
    assert eos.rag_status(value, 5, eos.LOWER_IS_BETTER, 0.1) == expected


def test_exactly_on_goal_is_green_both_directions() -> None:
    assert eos.rag_status(50, 50, eos.HIGHER_IS_BETTER) == "green"
    assert eos.rag_status(50, 50, eos.LOWER_IS_BETTER) == "green"


# --- To-Do 90% target ----------------------------------------------------------


def test_a_full_week_meets_the_target() -> None:
    score = eos.TodoScore(committed=10, done=10)
    assert score.rate == 1.0
    assert score.meets_target


def test_exactly_ninety_percent_meets_the_target() -> None:
    assert eos.TodoScore(committed=10, done=9).meets_target


def test_eighty_percent_misses() -> None:
    assert not eos.TodoScore(committed=10, done=8).meets_target


def test_an_empty_week_does_not_pass_by_vacuity() -> None:
    """0/0 is not 90% — an idle week must not flatter the number."""
    score = eos.TodoScore(committed=0, done=0)
    assert score.rate == 0.0
    assert not score.meets_target
