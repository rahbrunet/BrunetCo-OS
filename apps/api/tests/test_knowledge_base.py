"""Knowledge Base chunking, freshness and the licence guard (WP 6.8, §12.1) — no DB.

The licence tests are the ones with teeth. §12.1 curates third-party sources deliberately for
copyright hygiene, and "cite, don't reproduce" has to be enforced in code rather than remembered.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from py_shared.domain import knowledge_base as kb

MOPOP = """Introductory text before any section.

17.02 Double patenting
A claim is not patentable if it is not patentably distinct.
Further discussion of the doctrine follows here.

17.03 Obviousness-type double patenting
The second branch of the doctrine addresses obviousness.
"""


# --- chunking ------------------------------------------------------------------


def test_chunks_split_on_section_headings() -> None:
    chunks = kb.chunk_document(MOPOP, "MOPOP")
    citations = [c.citation for c in chunks]
    assert "MOPOP §17.02" in citations
    assert "MOPOP §17.03" in citations


def test_preamble_keeps_the_bare_source_citation() -> None:
    """Text before the first heading belongs to the document, not to a section that hasn't
    started — citing it as §17.02 would attribute it to the wrong provision."""
    chunks = kb.chunk_document(MOPOP, "MOPOP")
    assert chunks[0].citation == "MOPOP"


def test_a_chunk_never_spans_two_sections() -> None:
    """The citation is the point: a chunk straddling §17.02 and §17.03 cannot be attributed to
    either, and an agent citing it would be wrong about where the law came from."""
    chunks = kb.chunk_document(MOPOP, "MOPOP")
    for chunk in chunks:
        assert "17.03" not in chunk.body or chunk.citation == "MOPOP §17.03"


def test_heading_title_is_captured_as_the_heading_path() -> None:
    chunks = kb.chunk_document(MOPOP, "MOPOP")
    section = next(c for c in chunks if c.citation == "MOPOP §17.02")
    assert section.heading_path == "Double patenting"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("17.02 Double patenting", "17.02"),
        ("§ 17.02.01 Sub-provision", "17.02.01"),
        ("Section 28.2 Novelty", "28.2"),
        ("Rule 53(b) Continuations", "53(b)"),
    ],
)
def test_heading_forms_across_the_corpus(line: str, expected: str) -> None:
    text = f"{line}\nBody text for the section.\n"
    chunks = kb.chunk_document(text, "SRC")
    assert chunks[0].citation == f"SRC §{expected}"


def test_prose_beginning_with_a_number_is_not_treated_as_a_heading() -> None:
    """"2020 was a busy year…" must not become §2020 and hijack the citation of what follows."""
    text = (
        "17.02 Real heading\n"
        "2020 was a busy year for the applicant and the following discussion "
        "explains at some length why that mattered to the prosecution history.\n"
    )
    chunks = kb.chunk_document(text, "MOPOP")
    assert all(c.citation == "MOPOP §17.02" for c in chunks)


def test_long_sections_split_but_keep_their_citation() -> None:
    body = "\n\n".join(f"Paragraph {i} " + "x" * 400 for i in range(12))
    chunks = kb.chunk_document(f"17.02 Long section\n{body}\n", "MOPOP")
    assert len(chunks) > 1
    assert all(c.citation == "MOPOP §17.02" for c in chunks)


def test_split_pieces_stay_within_the_maximum() -> None:
    chunks = kb.chunk_document("17.02 H\n" + "x" * 20_000, "MOPOP")
    assert all(c.char_count <= kb.MAX_CHUNK_CHARS for c in chunks)


def test_ordinals_are_sequential_across_the_document() -> None:
    chunks = kb.chunk_document(MOPOP, "MOPOP")
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_content_hash_is_stable_and_change_sensitive() -> None:
    """Idempotent re-ingestion rests on this: same text, same hash, no re-chunking."""
    assert kb.content_hash("abc") == kb.content_hash("abc")
    assert kb.content_hash("abc") != kb.content_hash("abd")


# --- freshness -----------------------------------------------------------------


def _source(**kw: object) -> kb.Source:
    base = {
        "key": "mopop", "name": "MOPOP", "jurisdiction": "CA",
        "license_class": kb.LICENSE_OPEN, "edition_label": "2024-06",
        "refreshed_at": date(2026, 7, 1), "refresh_interval_days": 90,
    }
    base.update(kw)
    return kb.Source(**base)  # type: ignore[arg-type]


def test_a_recently_refreshed_source_is_fresh() -> None:
    assert not kb.is_stale(_source(), date(2026, 7, 18))


def test_a_source_past_its_interval_is_stale() -> None:
    assert kb.is_stale(_source(refreshed_at=date(2026, 1, 1)), date(2026, 7, 18))


def test_a_never_refreshed_source_is_stale() -> None:
    """"Registered the practice-notice feed and never fetched it" must read as stale — an agent
    grounding on an empty feed is worse off than one that knows it has nothing."""
    assert kb.is_stale(_source(refreshed_at=None), date(2026, 7, 18))


def test_a_source_with_no_interval_never_goes_stale_on_a_schedule() -> None:
    """A statute edition is stable; nagging about it trains people to ignore the staleness flag."""
    assert not kb.is_stale(
        _source(refresh_interval_days=None, refreshed_at=date(2020, 1, 1)), date(2026, 7, 18)
    )


def test_stale_sources_are_ordered_worst_first() -> None:
    sources = [
        _source(key="a", refreshed_at=date(2026, 6, 1)),
        _source(key="b", refreshed_at=None),
        _source(key="c", refreshed_at=date(2026, 1, 1)),
    ]
    stale = kb.stale_sources(sources, date(2026, 7, 18))
    assert [s.key for s in stale] == ["b", "c"]


# --- licence guard (§12.1 copyright hygiene) -----------------------------------


def _passage(license_class: str, body: str = "x" * 2_000, superseded: bool = False) -> kb.Passage:
    return kb.Passage(
        chunk_id=uuid4(), citation="MOPOP §17.02", heading_path="Double patenting",
        extract=kb._apply_license_guard(body, license_class),
        source_key="mopop", source_name="MOPOP", jurisdiction="CA",
        license_class=license_class, edition_label="2024-06",
        is_superseded=superseded, rank=0.5,
    )


def test_open_sources_are_quotable_in_full() -> None:
    passage = _passage(kb.LICENSE_OPEN)
    assert passage.may_quote
    assert len(passage.extract) == 2_000


def test_firm_content_is_quotable() -> None:
    assert _passage(kb.LICENSE_FIRM).may_quote


def test_third_party_material_is_truncated_to_a_grounding_extract() -> None:
    """§12.1: retrieved third-party content is used for grounding and cited, not reproduced."""
    passage = _passage(kb.LICENSE_GROUNDING_ONLY)
    assert not passage.may_quote
    assert len(passage.extract) <= kb.GROUNDING_ONLY_EXTRACT_CHARS + 6  # + the ellipsis marker
    assert passage.extract.endswith("[…]")


def test_a_short_third_party_passage_is_not_padded_or_marked() -> None:
    passage = _passage(kb.LICENSE_GROUNDING_ONLY, body="Short commentary.")
    assert passage.extract == "Short commentary."


def test_the_licence_guard_runs_at_retrieval_not_at_render() -> None:
    """Truncation happens when the passage is built, so no consumer reaches the full body by
    skipping the formatting helper."""
    guarded = kb._apply_license_guard("y" * 5_000, kb.LICENSE_GROUNDING_ONLY)
    assert len(guarded) < 5_000


def test_prompt_snippet_leads_with_the_citation() -> None:
    snippet = _passage(kb.LICENSE_OPEN, body="A claim is not patentable...").as_prompt_snippet()
    assert snippet.startswith("[MOPOP §17.02 — MOPOP, 2024-06]")


def test_prompt_snippet_warns_loudly_on_a_superseded_edition() -> None:
    snippet = _passage(kb.LICENSE_OPEN, superseded=True).as_prompt_snippet()
    assert "SUPERSEDED" in snippet


def test_prompt_snippet_tells_the_model_not_to_quote_third_party_material() -> None:
    snippet = _passage(kb.LICENSE_GROUNDING_ONLY).as_prompt_snippet()
    assert "do not quote" in snippet
