"""Shared redaction service (WP 6.9 §0, D45) — parity with the legacy production redactor.

No DB and no spaCy model required: the NER backend is a protocol, so these run everywhere CI does.
The fake recognizes entities from a fixed dictionary and returns character spans, matching the
real backend's contract — which is what lets the regex-beats-NER overlap resolution be tested.

The legacy service measured a 0% false-cancel rate over 213 real messages. The over-masking tests
below matter as much as the leak tests: a redactor that cancels good drafts gets routed around.
"""
from __future__ import annotations

import pytest
from py_shared import redaction


class FakeNer:
    """Deterministic stand-in for spaCy, returning spans for known surface forms.

    Entities present in the text but absent from the dictionary are simply not returned — which
    is how a real NER model behaves, and what global-occurrence masking exists to survive.
    """

    def __init__(self, entities: dict[str, str], available: bool = True) -> None:
        self._entities = entities
        self._available = available

    @property
    def name(self) -> str:
        return "fake:test"

    def available(self) -> bool:
        return self._available

    def entities(self, text: str) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for surface, label in self._entities.items():
            start = text.find(surface)
            while start != -1:
                spans.append((start, start + len(surface), label))
                start = text.find(surface, start + 1)
        return spans


class DeadNer(FakeNer):
    def __init__(self) -> None:
        super().__init__({}, available=False)


# --- fail-closed ---------------------------------------------------------------


def test_unavailable_ner_fails_closed() -> None:
    """D45: a missing model must stop the call, not silently degrade to regex-only."""
    with pytest.raises(redaction.RedactionUnavailable):
        redaction.redact("Call Jane Smith at Acme.", backend=DeadNer(), require_ner=True)


def test_regex_layer_still_runs_when_ner_is_explicitly_waived() -> None:
    """The two layers are independent: losing NER must not disable identifier masking."""
    result = redaction.redact(
        "Reach me at jane@acme.com or 613-555-0142.", backend=DeadNer(), require_ner=False,
    )
    assert "jane@acme.com" not in result.masked
    assert "613-555-0142" not in result.masked
    assert result.leaks == []


# --- global-occurrence masking -------------------------------------------------


def test_masks_every_occurrence_including_ones_ner_missed() -> None:
    text = "Acme Corp filed. We wrote to Acme Corp again later."
    result = redaction.redact(text, backend=FakeNer({"Acme Corp": "ORG"}))
    assert "Acme Corp" not in result.masked
    assert result.masked.count("ORG_001") == 2


def test_longer_entity_consumes_shorter_overlapping_one() -> None:
    """Masking short-first would leave "ORG_001 Corporation Ltd" — a still-identifying fragment."""
    text = "Acme Corporation Ltd and Acme are the same client."
    result = redaction.redact(
        text, backend=FakeNer({"Acme Corporation Ltd": "ORG", "Acme": "ORG"}),
    )
    assert "Acme" not in result.masked


def test_regex_wins_over_ner_on_an_overlapping_span() -> None:
    """An email containing a name: the regex span is more precise, so it claims the overlap and
    the address is masked whole rather than split around the name."""
    result = redaction.redact(
        "Write to jane.smith@acme.com today.", backend=FakeNer({"jane.smith": "PERSON"}),
    )
    assert "jane.smith@acme.com" not in result.masked
    assert "EMAIL_001" in result.masked
    assert result.leaks == []


# --- boundary-aware verification (the legacy false-cancel bug) -----------------


def test_person_named_mark_does_not_flag_the_word_marketing() -> None:
    """The regression that drove the legacy rate from ~89% false cancels to 0%."""
    text = "Mark asked about the Marketing budget."
    result = redaction.redact(text, backend=FakeNer({"Mark": "PERSON"}))
    assert "Marketing" in result.masked
    assert result.leaks == []


def test_entity_followed_by_punctuation_is_still_masked() -> None:
    result = redaction.redact("Regards, Acme Corp.", backend=FakeNer({"Acme Corp.": "ORG"}))
    assert "Acme Corp." not in result.masked


def test_verify_clean_reports_a_survivor() -> None:
    leaks = redaction.verify_clean("Contact Jane Smith today.", {"PERSON_001": "Jane Smith"})
    assert leaks == ["Jane Smith"]


# --- structured classes newly covered by the port -----------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "jane.smith@acme.com",
        "https://acme.com/private/matter",
        "www.swea-ip-law.se",
        "brunetco.com",
        "613-555-0142",
        "(613) 555-0142",
        "+44 20 7946 0958",
        "K1A 0B1",
        "90210-1234",
        "1200 Bank Street",
        "P.O. Box 4567",
    ],
)
def test_high_risk_identifiers_are_masked_without_ner_help(snippet: str) -> None:
    result = redaction.redact(f"Details: {snippet}", backend=FakeNer({}))
    assert snippet not in result.masked
    assert result.leaks == []


@pytest.mark.parametrize(
    "snippet",
    [
        "PCT/CA2019/050123",
        "WO 2020/123456 A1",
        "EP 1 234 567 B1",
        "US 9,876,543 B2",
        "CA 2,123,456",
        "Application No. 16/123,456",
        "Docket No. ABC-1234",
        "LRN-0001-0002",
        "$1,234.56",
        "CAD 2,500.00",
        "2025-06-09",
        "June 9, 2025",
        "9 June 2025",
        "06/09/2025",
    ],
)
def test_ip_and_financial_identifiers_are_masked(snippet: str) -> None:
    """Patent, application and docket numbers are among the most client-identifying tokens in an
    IP practice — the reason they are masked rather than treated as domain vocabulary."""
    result = redaction.redact(f"Re: {snippet}", backend=FakeNer({}))
    assert snippet not in result.masked


def test_email_is_not_shredded_by_the_bare_domain_pattern() -> None:
    result = redaction.redact("write to jane@acme.com now", backend=FakeNer({}))
    assert "EMAIL_001" in result.masked
    assert "DOMAIN" not in result.masked


# --- the guard set is deliberately narrower than the mask set -----------------


def test_guard_covers_only_high_risk_classes() -> None:
    """Masked-but-not-guarded is the key distinction: a surviving date is not a privacy breach,
    and cancelling on one would ground the drafter permanently."""
    guarded = {kind for kind, _ in redaction.GUARD_PATTERNS}
    assert guarded == {"email", "url", "phone", "address", "postcode"}


def test_a_surviving_date_does_not_cancel_the_call() -> None:
    assert redaction.scan_structured_identifiers("Due 2025-06-09, fee $500.") == []


def test_a_surviving_email_does_cancel_the_call() -> None:
    hits = redaction.scan_structured_identifiers("Reply to jane@acme.com")
    assert any(h.startswith("email:") for h in hits)


# --- over-masking guards (false-cancel prevention) -----------------------------


def test_bare_five_digit_zip_is_not_masked() -> None:
    """Deliberate: a bare 5-digit ZIP is indistinguishable from any other 5-digit number, and
    masking it was judged too noisy. ZIP+4 is distinctive enough."""
    result = redaction.redact("Batch 90210 processed.", backend=FakeNer({}))
    assert "90210" in result.masked


@pytest.mark.parametrize("snippet", ["Fig.3", "i.e.", "Brunet & Co."])
def test_domain_pattern_does_not_fire_on_ordinary_prose(snippet: str) -> None:
    """The TLD allowlist exists for exactly these — a bare `\\w+\\.\\w+` pattern here was a
    false-cancel source."""
    assert redaction.scan_structured_identifiers(snippet) == []


@pytest.mark.parametrize("acronym", ["CIPO", "PCT", "WIPO", "USPTO", "EPO", "IDS"])
def test_ip_acronyms_pass_through_in_cleartext(acronym: str) -> None:
    result = redaction.redact(f"{acronym} issued the report.", backend=FakeNer({acronym: "ORG"}))
    assert acronym in result.masked


def test_generic_jurisdictions_pass_through_as_locations() -> None:
    result = redaction.redact(
        "Filed in Canada and Ontario.",
        backend=FakeNer({"Canada": "LOCATION", "Ontario": "LOCATION"}),
    )
    assert "Canada" in result.masked and "Ontario" in result.masked


def test_common_geo_exemption_does_not_extend_to_organizations() -> None:
    """A client named "Alberta Holdings" must still mask — the exemption is LOCATION-only."""
    result = redaction.redact(
        "Acting for Alberta Holdings.", backend=FakeNer({"Alberta Holdings": "ORG"}),
    )
    assert "Alberta Holdings" not in result.masked


def test_prompt_scaffolding_is_never_masked() -> None:
    """NER false-positives on our own section headers would trip the leak guard."""
    result = redaction.redact(
        "STYLE EXAMPLES\nATTACHMENTS", backend=FakeNer({"STYLE EXAMPLES": "ORG"}),
    )
    assert "STYLE EXAMPLES" in result.masked


def test_firm_principals_stay_unmasked_so_the_voice_survives() -> None:
    result = redaction.redact(
        "Robert Brunet acting for Jane Smith.",
        backend=FakeNer({"Robert Brunet": "PERSON", "Jane Smith": "PERSON"}),
        allow_entities=["Robert Brunet"],
    )
    assert "Robert Brunet" in result.masked
    assert "Jane Smith" not in result.masked


def test_a_client_entity_containing_the_principals_surname_still_masks() -> None:
    """Only FULL principal forms are allow-listed. Listing the bare surname would leave
    "H. Brunet Family Trust" — a real client — in cleartext."""
    result = redaction.redact(
        "Acting for H. Brunet Family Trust.",
        backend=FakeNer({"H. Brunet Family Trust": "ORG"}),
        allow_entities=["Robert Brunet", "Brunet & Co."],
    )
    assert "H. Brunet Family Trust" not in result.masked


# --- known-entity dictionary ---------------------------------------------------


def test_known_entities_are_masked_even_when_ner_misses_them_entirely() -> None:
    result = redaction.redact(
        "Regarding Zenithal Dynamics.", backend=FakeNer({}),
        known_entities=["Zenithal Dynamics"],
    )
    assert "Zenithal Dynamics" not in result.masked
    assert "CLIENT_001" in result.masked


def test_a_known_entity_absent_from_the_text_does_not_inflate_the_mapping() -> None:
    result = redaction.redact(
        "Regarding Zenithal Dynamics.", backend=FakeNer({}),
        known_entities=["Absent Co", "Zenithal Dynamics"],
    )
    assert len(result.mapping) == 1
    assert "CLIENT_001" in result.masked


# --- round-trip ----------------------------------------------------------------


def test_rehydrate_restores_exactly_what_was_masked() -> None:
    text = "Jane Smith at Acme Corp, jane@acme.com, re the filing."
    result = redaction.redact(
        text, backend=FakeNer({"Jane Smith": "PERSON", "Acme Corp": "ORG"}),
    )
    assert redaction.rehydrate(result.masked, result.mapping) == text


def test_rehydrate_does_not_substitute_inside_a_longer_placeholder() -> None:
    mapping = {f"PERSON_{i:03d}": f"Name{i}" for i in range(1, 12)}
    mapping["PERSON_0011"] = "Eleventh"
    assert redaction.rehydrate("PERSON_0011", mapping) == "Eleventh"


def test_the_same_value_always_gets_the_same_placeholder() -> None:
    result = redaction.redact(
        "Acme Corp and Acme Corp.", backend=FakeNer({"Acme Corp": "ORG"}),
        known_entities=["Acme Corp"],
    )
    assert len(result.mapping) == 1


def test_placeholders_never_trip_the_guard_patterns() -> None:
    """The property the legacy notes rely on: placeholders carry no '@', scheme, TLD or
    qualifying digit run, so a fully-masked message cannot cancel itself."""
    masked = "PERSON_001 at ORG_002, EMAIL_003, PHONE_004, ADDRESS_005, POSTCODE_006"
    assert redaction.scan_structured_identifiers(masked) == []
