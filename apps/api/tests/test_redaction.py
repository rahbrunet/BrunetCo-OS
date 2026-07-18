"""Shared redaction service (WP 6.9 §0, D45) — the properties that make it safe to send anything.

No DB and no spaCy model required: the NER backend is a protocol, so these run everywhere CI does.
The fake recognizes entities from a fixed dictionary, which is exactly the property under test —
the redactor must be correct given a recognizer, and safe given none.
"""
from __future__ import annotations

import pytest
from py_shared import redaction


class FakeNer:
    """Deterministic stand-in for spaCy. `misses` are entities present in the text that the
    recognizer does NOT return — how a real NER model behaves, and what global-occurrence
    masking exists to survive."""

    def __init__(self, entities: dict[str, str], available: bool = True) -> None:
        self._entities = entities
        self._available = available

    @property
    def name(self) -> str:
        return "fake:test"

    def available(self) -> bool:
        return self._available

    def entities(self, text: str) -> list[tuple[str, str]]:
        return [(v, label) for v, label in self._entities.items() if v in text]


class DeadNer(FakeNer):
    def __init__(self) -> None:
        super().__init__({}, available=False)


# --- fail-closed ---------------------------------------------------------------


def test_unavailable_ner_fails_closed() -> None:
    """The whole point of D45: a missing model must stop the call, not weaken it."""
    with pytest.raises(redaction.RedactionUnavailable):
        redaction.redact("Call Jane Smith at Acme.", backend=DeadNer(), require_ner=True)


def test_unavailable_ner_still_masks_structured_when_explicitly_permitted() -> None:
    """With the fail-closed switch off (dev sandbox only), the regex backstop still runs — the
    two mechanisms are independent, so losing NER must not disable identifier masking."""
    result = redaction.redact(
        "Reach me at jane@acme.com or 613-555-0142.", backend=DeadNer(), require_ner=False,
    )
    assert "jane@acme.com" not in result.masked
    assert "613-555-0142" not in result.masked
    assert result.leaks == []


# --- global-occurrence masking -------------------------------------------------


def test_masks_every_occurrence_including_ones_ner_missed() -> None:
    """NER tags "Acme Corp" once; the second mention must vanish too. Recall-limited recognition
    is the normal case, so occurrence matching — not the model — carries the guarantee."""
    text = "Acme Corp filed on Monday. We wrote to Acme Corp again on Friday."
    result = redaction.redact(text, backend=FakeNer({"Acme Corp": "ORG"}))
    assert "Acme Corp" not in result.masked
    assert result.masked.count("[ORG_1]") == 2


def test_longer_entity_consumes_shorter_overlapping_one() -> None:
    """Masking short-first would leave "[ORG_1] Corporation" — a fragment that still identifies."""
    text = "Acme Corporation Ltd and Acme are the same client."
    result = redaction.redact(
        text, backend=FakeNer({"Acme Corporation Ltd": "ORG", "Acme": "ORG"}),
    )
    assert "Acme" not in result.masked


# --- boundary-aware verification (the legacy false-cancel bug) -----------------


def test_person_named_mark_does_not_flag_the_word_marketing() -> None:
    """The regression that mattered: substring matching cancelled real drafts because "Marketing"
    contains "Mark". A false cancel teaches users to route around the safety control."""
    text = "Mark asked about the Marketing budget."
    result = redaction.redact(text, backend=FakeNer({"Mark": "PERSON"}))
    assert "Marketing" in result.masked
    assert result.leaks == []


def test_entity_followed_by_punctuation_is_still_masked() -> None:
    r"""A naive `\b...\b` never matches a value ending in "."; the lookaround form does."""
    result = redaction.redact("Regards, Acme Corp.", backend=FakeNer({"Acme Corp.": "ORG"}))
    assert "Acme Corp." not in result.masked


def test_verify_clean_reports_a_survivor() -> None:
    leaks = redaction.verify_clean("Contact Jane Smith today.", {"[PERSON_1]": "Jane Smith"})
    assert leaks == ["Jane Smith"]


# --- structured backstop -------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "jane.smith@acme.com",
        "https://acme.com/private/matter",
        "613-555-0142",
        "K1A 0B1",
        "1200 Bank Street",
    ],
)
def test_structured_identifiers_are_caught_without_any_ner_help(snippet: str) -> None:
    """Planted identifiers the recognizer knows nothing about. This is the "one failing mechanism
    cannot mask the other's gap" property stated concretely."""
    result = redaction.redact(f"Details: {snippet}", backend=FakeNer({}))
    assert snippet not in result.masked
    assert result.leaks == []


def test_email_is_not_shredded_by_the_bare_domain_pattern() -> None:
    """Pattern order matters: a DOMAIN match inside an address would leave "jane@[DOMAIN_1]"."""
    found = dict(redaction.scan_structured_identifiers("write to jane@acme.com now"))
    assert found == {"jane@acme.com": "EMAIL"}


# --- stoplist / allow-list -----------------------------------------------------


def test_public_institutions_are_not_masked() -> None:
    """Masking "CIPO" protects nobody and costs the model the context it needs to draft sensibly."""
    result = redaction.redact(
        "CIPO issued the report.", backend=FakeNer({"CIPO": "ORG"}),
    )
    assert "CIPO" in result.masked


def test_firm_principals_stay_unmasked_so_the_voice_survives() -> None:
    result = redaction.redact(
        "Robert Brunet acting for Jane Smith.",
        backend=FakeNer({"Robert Brunet": "PERSON", "Jane Smith": "PERSON"}),
        allow_entities=["Robert Brunet"],
    )
    assert "Robert Brunet" in result.masked
    assert "Jane Smith" not in result.masked


def test_known_entities_are_masked_even_when_ner_misses_them_entirely() -> None:
    """Client names come from the DB — their masking must not depend on the model noticing."""
    result = redaction.redact(
        "Regarding Zenithal Dynamics.", backend=FakeNer({}),
        known_entities=["Zenithal Dynamics"],
    )
    assert "Zenithal Dynamics" not in result.masked


def test_stale_known_entity_does_not_burn_a_placeholder_number() -> None:
    """A client name absent from this text must not shift the numbering of the ones present."""
    result = redaction.redact(
        "Regarding Zenithal Dynamics.", backend=FakeNer({}),
        known_entities=["Absent Co", "Zenithal Dynamics"],
    )
    assert "[PERSON_1]" in result.masked


# --- round-trip ----------------------------------------------------------------


def test_rehydrate_restores_exactly_what_was_masked() -> None:
    text = "Jane Smith at Acme Corp, jane@acme.com, re the filing."
    result = redaction.redact(
        text, backend=FakeNer({"Jane Smith": "PERSON", "Acme Corp": "ORG"}),
    )
    assert redaction.rehydrate(result.masked, result.mapping) == text


def test_rehydrate_does_not_substitute_inside_a_longer_placeholder() -> None:
    """[PERSON_1] must not be replaced inside [PERSON_11] — hence longest-first replacement."""
    mapping = {f"[PERSON_{i}]": f"Name{i}" for i in range(1, 12)}
    assert redaction.rehydrate("[PERSON_11]", mapping) == "Name11"


def test_same_value_under_two_labels_gets_one_placeholder() -> None:
    """Two placeholders for one value would tell the model the two mentions are different people."""
    result = redaction.redact(
        "Acme Corp and Acme Corp.", backend=FakeNer({"Acme Corp": "ORG"}),
        known_entities=["Acme Corp"],
    )
    assert len(result.mapping) == 1
