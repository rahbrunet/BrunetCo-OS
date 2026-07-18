"""Shared redaction service (WP 6.9 §0, D45) — the single de-identification choke point.

Every OS agent that sends firm data to an external LLM calls this first. Ported from the legacy
`/opt/email-assistant/redactor.py`, which has run in production with a measured 0% false-cancel
rate over 213+ real messages. The properties below are the reason it achieves that; none of them
are incidental, so none of them may be "simplified" away.

  * Global-occurrence masking. When the NER model tags "Acme Corp" in one sentence, every other
    occurrence in the text is masked too — including the ones the model missed. Entity recognition
    is recall-limited; occurrence matching is not.

  * Boundary-aware leak verification. `verify_clean` re-scans the masked text for the original
    values using word boundaries. Substring matching was the source of the legacy false cancels:
    masking a person named "Mark" made every "Marketing" look like a leak, and a cancelled draft
    trains users to stop using the tool.

  * An independent structured-identifier backstop. Emails, URLs, domains, phone numbers and
    postcodes are matched by regex, entirely separately from the NER path. Two mechanisms with
    uncorrelated failure modes: a missing spaCy model cannot silently disable identifier masking,
    and a regex gap cannot silently disable name masking.

  * Fail-closed. If the NER backend is unavailable and `require_ner` is set, `redact` raises
    rather than returning weakly-masked text. The caller's only options become "cancel" or
    "cancel and alert" — never "send it anyway". The egress gate (WP 6.1) enforces the same
    invariant one layer out: no redaction ref, no LLM call.

The mapping never leaves the process. `redact` returns it for the `rehydrate` round-trip and the
audit records only label counts — see migration 0015 for why persisting it would be self-defeating.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import psycopg
from psycopg.types.json import Json

# ---------------------------------------------------------------------------
# NER backend (pluggable; spaCy in prod, a deterministic fake in tests)
# ---------------------------------------------------------------------------


class NerBackend(Protocol):
    """Minimal contract the redactor needs from an entity recognizer."""

    @property
    def name(self) -> str:
        """Backend identity recorded in the audit ('spacy:en_core_web_md')."""
        ...

    def available(self) -> bool:
        """False when the model cannot be loaded — triggers the fail-closed path."""
        ...

    def entities(self, text: str) -> list[tuple[str, str]]:
        """(surface_text, label) pairs. Labels follow spaCy's scheme (PERSON/ORG/GPE/...)."""
        ...


# Labels worth masking. spaCy emits plenty more (CARDINAL, ORDINAL, DATE...) that carry no
# identity and whose masking would shred the text's meaning for the model.
MASKED_LABELS = frozenset({"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP"})

# spaCy reliably mislabels these as ORG/PERSON in legal correspondence. Masking them costs the
# model real context ("the Patent Office" -> "[ORG_3]") and protects nothing — they are public
# institutions and terms of art, not client identities.
_STOPLIST = frozenset({
    "cipo", "uspto", "epo", "wipo", "euipo", "patent office", "the patent office",
    "canadian intellectual property office", "united states patent and trademark office",
    "european patent office", "pct", "madrid", "paris convention", "mopop", "mpep", "tmep",
    "trademarks journal", "patent act", "trademarks act", "federal court", "supreme court",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
    "re", "fw", "fwd", "cc", "bcc", "attn", "ltd", "inc", "llp", "llc", "corp",
})


class SpacyNerBackend:
    """Production backend. Prefers `en_core_web_md` (the legacy service's model) and falls back to
    `en_core_web_sm`; if neither is installed it reports unavailable and the fail-closed path
    takes over. spaCy is an optional dependency precisely so this file stays importable — and the
    structured backstop stays testable — on a machine without the model."""

    _MODELS = ("en_core_web_md", "en_core_web_sm")

    def __init__(self) -> None:
        self._nlp: object | None = None
        self._name = "spacy:unavailable"
        try:
            import spacy
        except ImportError:
            return
        for model in self._MODELS:
            try:
                self._nlp = spacy.load(model)
                self._name = f"spacy:{model}"
                return
            except OSError:
                continue

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return self._nlp is not None

    def entities(self, text: str) -> list[tuple[str, str]]:
        if self._nlp is None:
            return []
        doc = self._nlp(text)  # type: ignore[operator]
        return [(ent.text, ent.label_) for ent in doc.ents]


# ---------------------------------------------------------------------------
# Structured identifiers (independent of NER by design)
# ---------------------------------------------------------------------------

# Ordered: email before URL before bare domain, so the most specific pattern claims the span and
# a bare-domain match cannot chew the tail off an address.
_STRUCTURED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("URL", re.compile(r"https?://[^\s<>\"')]+")),
    ("DOMAIN", re.compile(r"\b(?:[\w-]+\.)+(?:com|ca|org|net|io|co\.uk|gov|edu)\b", re.I)),
    ("PHONE", re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")),
    # Canadian postal code (A1A 1A1) and US ZIP/ZIP+4.
    ("POSTCODE", re.compile(r"\b(?:[A-Z]\d[A-Z][ -]?\d[A-Z]\d|\d{5}(?:-\d{4})?)\b")),
    ("ADDRESS", re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][\w.'-]*\s+){0,3}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way|Suite|Ste)\b",
        re.I,
    )),
)


def scan_structured_identifiers(text: str) -> list[tuple[str, str]]:
    """(value, label) for every structured identifier in ``text``.

    Used twice, and the duplication is the point: once by `redact` to mask these values, and again
    by the caller (or `verify_clean`) against the *masked* text as a backstop. Because it shares no
    code path with the NER backend, a failure in one cannot mask a gap in the other.
    """
    found: list[tuple[str, str]] = []
    claimed: list[tuple[int, int]] = []
    for label, pattern in _STRUCTURED_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue  # a more specific pattern already owns this span
            claimed.append((start, end))
            found.append((match.group(0), label))
    return found


# ---------------------------------------------------------------------------
# Result types + errors
# ---------------------------------------------------------------------------


class RedactionUnavailable(RuntimeError):
    """The NER backend is unavailable and `require_ner` is set — fail closed, do not send."""


class RedactionLeak(RuntimeError):
    """Verification found an original value surviving in the masked text — do not send."""


@dataclass
class RedactionResult:
    masked: str
    # placeholder -> original value. Process-local; never persisted (see migration 0015).
    mapping: dict[str, str] = field(default_factory=dict)
    ref: str = ""
    backend: str = ""
    entity_counts: dict[str, int] = field(default_factory=dict)
    structured_hits: int = 0
    leaks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _word_boundary_pattern(value: str) -> re.Pattern[str]:
    r"""Case-insensitive whole-token match for ``value``.

    `\b` is only a boundary next to a word character, so a value ending in punctuation
    ("Acme Corp.") would never match with a naive `\b...\b`. Anchoring on lookarounds for
    word characters handles both shapes.
    """
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)


def _should_mask(value: str) -> bool:
    cleaned = value.strip()
    if len(cleaned) < 2:
        return False  # single characters are initials/artifacts; masking them shreds the text
    return cleaned.lower().strip(".,;:") not in _STOPLIST


def redact(
    text: str,
    backend: NerBackend | None = None,
    known_entities: Iterable[str] = (),
    allow_entities: Iterable[str] = (),
    require_ner: bool = True,
) -> RedactionResult:
    """De-identify ``text``, returning the masked text plus the mapping needed to rehydrate.

    ``known_entities`` are values the caller knows are sensitive (client and matter party names
    pulled from the DB) — masked unconditionally, since they must not depend on the model
    noticing them. ``allow_entities`` are values deliberately left in place: the firm's own
    principals, whose names appear in every signature and whose masking would strip the voice the
    drafter is trying to imitate without protecting a third party.

    Masking is applied longest-value-first so that "Acme Corporation Ltd" is consumed before
    "Acme", which would otherwise leave a "[ORG_1] Corporation Ltd" fragment behind.
    """
    backend = backend or SpacyNerBackend()
    if not backend.available():
        if require_ner:
            raise RedactionUnavailable(
                f"NER backend {backend.name!r} unavailable — refusing to redact (D45 fail-closed)"
            )

    allowed = {a.strip().lower() for a in allow_entities if a.strip()}

    # Collect candidates: caller-known values, NER entities, then structured identifiers.
    candidates: dict[str, str] = {}  # value -> label

    for value in known_entities:
        if _should_mask(value) and value.strip().lower() not in allowed:
            candidates[value.strip()] = "PERSON"

    for surface, label in backend.entities(text):
        if label not in MASKED_LABELS:
            continue
        value = surface.strip()
        if not _should_mask(value) or value.lower() in allowed:
            continue
        candidates.setdefault(value, label)

    structured = scan_structured_identifiers(text)
    for value, label in structured:
        if value.strip().lower() in allowed:
            continue
        candidates[value.strip()] = label

    # Apply masks, longest first so short values cannot fragment longer ones.
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    masked = text
    for value in sorted(candidates, key=len, reverse=True):
        label = candidates[value]
        # Same value seen under two labels (a client that is both ORG and a known entity) reuses
        # one placeholder — two placeholders for one value would tell the model they differ.
        existing = next((ph for ph, orig in mapping.items() if orig.lower() == value.lower()), None)
        if existing is not None:
            continue
        counters[label] = counters.get(label, 0) + 1
        placeholder = f"[{label}_{counters[label]}]"
        pattern = _word_boundary_pattern(value)
        new_masked = pattern.sub(placeholder, masked)  # global-occurrence masking
        if new_masked == masked:
            # Value not actually present (a stale known-entity): don't burn a placeholder number.
            counters[label] -= 1
            continue
        masked = new_masked
        mapping[placeholder] = value

    result = RedactionResult(
        masked=masked,
        mapping=mapping,
        ref=f"red_{uuid.uuid4().hex}",
        backend=backend.name,
        entity_counts=dict(counters),
        structured_hits=len(structured),
    )
    result.leaks = verify_clean(result.masked, result.mapping)
    return result


def verify_clean(masked: str, mapping: dict[str, str]) -> list[str]:
    """Original values still present in ``masked``, as whole tokens.

    Boundary-aware for the reason in the module docstring: substring matching turned a person
    named "Mark" into a leak report on the word "Marketing", and every false cancel is a user
    learning to route around the safety control. Also re-runs the structured backstop, so an
    identifier the NER path missed and the regex pass somehow skipped still surfaces here rather
    than on the wire.
    """
    leaks = [value for value in mapping.values() if _word_boundary_pattern(value).search(masked)]
    leaks.extend(value for value, _ in scan_structured_identifiers(masked))
    return sorted(set(leaks))


def rehydrate(text: str, mapping: dict[str, str]) -> str:
    """Restore original values in the model's response (the inverse of `redact`).

    Longest placeholder first so `[PERSON_1]` cannot be replaced inside `[PERSON_11]`.
    """
    restored = text
    for placeholder in sorted(mapping, key=len, reverse=True):
        restored = restored.replace(placeholder, mapping[placeholder])
    return restored


def ner_available(backend: NerBackend | None = None) -> bool:
    """Whether entity recognition is usable right now. Ops surfaces this: an unavailable model
    means every LLM-backed agent is failing closed, which is safe but silent without a signal."""
    return (backend or SpacyNerBackend()).available()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def record_redaction(
    conn: psycopg.Connection, agent_name: str, result: RedactionResult,
) -> str:
    """Persist the audit row and return the reference the egress gate will demand.

    Counts and the leak verdict only — never the mapping. An auditor asking "did redaction run
    before this call, and did it come back clean?" is answered in full by this row; an auditor
    asking "what were the real names?" is asking for the thing the service exists to withhold.
    """
    conn.execute(
        """
        insert into ops.redaction_events
          (ref, agent_name, backend, entity_counts, structured_hits, leaks)
        values (%s, %s, %s, %s::jsonb, %s, %s)
        """,
        (result.ref, agent_name, result.backend, Json(result.entity_counts),
         result.structured_hits, len(result.leaks)),
    )
    return result.ref


def redact_for_egress(
    conn: psycopg.Connection,
    agent_name: str,
    text: str,
    backend: NerBackend | None = None,
    known_entities: Sequence[str] = (),
    allow_entities: Sequence[str] = (),
) -> RedactionResult:
    """Redact, audit, and refuse on leak — the entry point every LLM caller should use.

    Bundling the three steps is what makes the safe path also the easy path: a caller who wants a
    redaction ref (and without one the egress gate rejects them) cannot obtain it without having
    passed verification.
    """
    result = redact(text, backend=backend, known_entities=known_entities,
                    allow_entities=allow_entities, require_ner=True)
    record_redaction(conn, agent_name, result)
    if result.leaks:
        raise RedactionLeak(
            f"{len(result.leaks)} value(s) survived redaction — refusing egress (ref {result.ref})"
        )
    return result
