"""Pure-grammar unit tests for the Appendix A reference generator (no DB).

Proves the ordered jurisdiction-segment rule, the USP/US independence, PCT/MP-as-bases,
the TM/Design display tag, and per-client scheme templates — exhaustively and fast.
"""
from __future__ import annotations

from py_shared.domain.references import (
    family_display_reference,
    family_reference,
    matter_reference,
    next_segment,
)

# --- Ordered jurisdiction segments -----------------------------------------

def test_first_filing_is_bare_code() -> None:
    assert next_segment("US", []) == "US"


def test_sequence_runs_us_us2_us3() -> None:
    assert next_segment("US", ["US"]) == "US2"
    assert next_segment("US", ["US", "US2"]) == "US3"
    assert next_segment("US", ["US", "US2", "US3"]) == "US4"


def test_sequence_reaches_production_highs() -> None:
    # Observed live: US11, CA5, EP2, AU2, MX3.
    assert next_segment("US", [f"US{i}" if i > 1 else "US" for i in range(1, 12)]) == "US12"
    assert next_segment("CA", ["CA", "CA2", "CA3", "CA4", "CA5"]) == "CA6"
    assert next_segment("EP", ["EP"]) == "EP2"


def test_usp_is_an_independent_track() -> None:
    # A US provisional does NOT consume a number in the regular US sequence.
    existing = ["USP", "US", "US2"]
    assert next_segment("US", existing) == "US3"     # regular US sequence unaffected by USP
    assert next_segment("USP", existing) == "USP2"   # provisional has its own track


def test_us_base_does_not_match_usp() -> None:
    assert next_segment("US", ["USP"]) == "US"       # USP present, no regular US yet


def test_pct_and_mp_are_ordinary_bases() -> None:
    # PCT/MP are sibling vehicle matters, sequenced like any other base.
    assert next_segment("PCT", []) == "PCT"
    assert next_segment("MP", []) == "MP"
    assert next_segment("PCT", ["PCT"]) == "PCT2"


def test_segments_are_independent_across_bases() -> None:
    family = ["USP", "US", "US2", "CA", "PCT", "MP"]
    assert next_segment("US", family) == "US3"
    assert next_segment("CA", family) == "CA2"
    assert next_segment("PCT", family) == "PCT2"
    assert next_segment("JP", family) == "JP"


# --- Reference strings ------------------------------------------------------

def test_family_reference_standard() -> None:
    assert family_reference("3DB", "0001") == "3DB-0001"


def test_family_reference_honors_per_client_scheme() -> None:
    # e.g. NRC-2019-016 year-based scheme, ARL three-segment scheme (Appendix A).
    assert family_reference("NRC", "2019-016", "{client}-{seq}") == "NRC-2019-016"
    assert family_reference("ARL", "004-1358", "{client}-{seq}") == "ARL-004-1358"


def test_matter_reference_appends_segment() -> None:
    assert matter_reference("3DB-0001", "US2") == "3DB-0001-US2"
    assert matter_reference("ARL-004-1358", "PCT") == "ARL-004-1358-PCT"


def test_advisory_matter_has_no_segment() -> None:
    assert matter_reference("3DB-0007", "") == "3DB-0007"


# --- TM/Design display tag (Appendix A) -------------------------------------

def test_trademark_word_mark_tagged_tm() -> None:
    assert family_display_reference("3DB-0002", "trademark", tm_design=False) == "3DB-0002 (TM)"


def test_trademark_design_mark_tagged_design() -> None:
    assert family_display_reference("3DB-0002", "trademark", tm_design=True) == "3DB-0002 Design"


def test_patent_and_advisory_untagged() -> None:
    assert family_display_reference("3DB-0001", "patent", tm_design=False) == "3DB-0001"
    assert family_display_reference("3DB-0009", "advisory", tm_design=False) == "3DB-0009"
