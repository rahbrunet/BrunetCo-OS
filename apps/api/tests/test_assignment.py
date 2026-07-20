"""Assignment scoring (WP 5.2, §M9) — pure, no DB.

The scoring is a legible sort key on purpose: an owner overruling a suggestion should see why it
was made. These tests pin the priority order — capacity, then load, then speed — so a future
tweak that quietly promotes "fastest" over "least loaded" fails loudly.
"""
from __future__ import annotations

from uuid import uuid4

from py_shared.domain import assignment as asg


def _cand(name: str, load: int, cap: int | None = None,
          cycle: float | None = None, baseline: float | None = None) -> asg.Candidate:
    return asg.Candidate(
        user_id=uuid4(), display_name=name, open_load=load, max_concurrent=cap,
        avg_cycle_days=cycle, baseline_cycle_days=baseline,
    )


# --- load is the driver --------------------------------------------------------


def test_the_least_loaded_candidate_wins() -> None:
    ranked = asg.rank_candidates([_cand("Busy", 8), _cand("Free", 1), _cand("Mid", 4)])
    assert [c.display_name for c in ranked] == ["Free", "Mid", "Busy"]


def test_an_at_capacity_candidate_is_deprioritised_below_everyone_under_cap() -> None:
    """Never pile onto someone already full while an under-cap colleague is free — even if the
    full person would otherwise have the lower raw load."""
    full = _cand("Full", load=3, cap=3)          # at cap
    roomy = _cand("Roomy", load=5, cap=20)        # more work, but room to spare
    ranked = asg.rank_candidates([full, roomy])
    assert ranked[0].display_name == "Roomy"


def test_capacity_uses_the_soft_cap_when_none_is_set() -> None:
    """No explicit cap doesn't mean infinite room — the scorer still balances load."""
    a = _cand("A", load=2)
    b = _cand("B", load=9)
    assert asg.rank_candidates([b, a])[0].display_name == "A"


# --- cycle time is the tie-break, not the driver -------------------------------


def test_cycle_time_only_breaks_a_load_tie() -> None:
    slow_but_free = _cand("Free", load=2, cycle=10.0)
    fast_but_busy = _cand("Busy", load=6, cycle=1.0)
    # Load dominates: the free-but-slower person is still the better pick.
    assert asg.rank_candidates([fast_but_busy, slow_but_free])[0].display_name == "Free"


def test_at_equal_load_the_faster_history_wins() -> None:
    slow = _cand("Slow", load=3, cycle=9.0)
    fast = _cand("Fast", load=3, cycle=2.0)
    assert asg.rank_candidates([slow, fast])[0].display_name == "Fast"


def test_no_history_ranks_at_the_baseline_not_last() -> None:
    """A newcomer with no track record ties with an average performer, rather than being buried —
    otherwise nobody new ever gets assigned and the history never accrues."""
    newcomer = _cand("New", load=3, cycle=None, baseline=5.0)
    average = _cand("Avg", load=3, cycle=5.0)
    faster = _cand("Fast", load=3, cycle=2.0)
    ranked = asg.rank_candidates([newcomer, average, faster])
    assert ranked[0].display_name == "Fast"
    # Newcomer and average are interchangeable on cycle; name breaks the tie deterministically.
    assert {ranked[1].display_name, ranked[2].display_name} == {"New", "Avg"}


# --- determinism ---------------------------------------------------------------


def test_ordering_is_stable_via_the_name_tiebreak() -> None:
    a = _cand("Alice", load=3)
    b = _cand("Bob", load=3)
    first = [c.display_name for c in asg.rank_candidates([b, a])]
    second = [c.display_name for c in asg.rank_candidates([a, b])]
    assert first == second == ["Alice", "Bob"]


def test_at_capacity_property() -> None:
    assert _cand("x", load=3, cap=3).at_capacity
    assert _cand("x", load=2, cap=3).at_capacity is False
    assert _cand("x", load=99).at_capacity is False   # no cap set
