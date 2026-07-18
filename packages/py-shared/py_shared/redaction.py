"""Shared redaction service (WP 6.9 §0, D45) — the single de-identification choke point.

Every OS agent that sends firm data to an external LLM calls this first. Ported from the legacy
`/opt/email-assistant/src/redactor.py`, which has run in production since 2026-06-23 with a
measured **0% false-cancel rate over 213 real messages** — down from ~89% with an earlier
span-based implementation. That number is the whole point: a redactor that cancels legitimate
drafts trains users to route around the safety control, so over-masking is a real failure mode
and not merely an inconvenience.

The properties below are what earned that rate. None are incidental.

  * **Two independent detection layers.** Regex for structured identifiers, spaCy NER for
    names/orgs/locations, plus a caller-supplied known-entity dictionary that masks regardless of
    what either layer noticed. Regex wins on overlap — it is the more precise of the two.

  * **Global-occurrence masking.** An entity tagged once is masked everywhere in the text,
    including the quoted chain and headers. Span-only substitution was the root cause of the
    legacy false-cancel rate: untagged repeats survived and tripped the leak guard.

  * **Boundary-aware leak verification.** `verify_clean` re-scans for mapped values as whole
    tokens. Substring matching made a person named "Mark" flag every "Marketing".

  * **A separate, narrower guard pattern set.** `MASK_PATTERNS` decides what gets masked;
    `GUARD_PATTERNS` decides what *cancels the call*. The guard covers only high-risk classes
    (email/URL/domain/phone/address/postcode). Dates, money and patent numbers are masked but
    deliberately do NOT cancel — a date surviving in cleartext is not a privacy breach, and
    cancelling on one would ground the drafter permanently.

  * **Fail-closed.** No NER model and `require_ner` set means no redaction and no call.

Deliberate deviations from the legacy service, both documented in the WP 6.9 tracker row:
  * the NER backend is a protocol rather than a hard spaCy import, so this module stays
    importable — and the regex layer stays testable — without the ~800 MB model present;
  * the known-entity dictionary is passed in by the caller (from the OS matter/client tables)
    instead of read from `data/known_entities.txt` on disk.

The mapping never leaves the process. `redact` returns it for the `rehydrate` round-trip; the
audit records label counts only — see migration 0015 for why persisting it would be self-defeating.
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

    def entities(self, text: str) -> list[tuple[int, int, str]]:
        """(start_char, end_char, label) spans. Offsets, not surface text, so the caller can
        resolve regex/NER overlaps by position exactly as the legacy service does."""
        ...


# spaCy labels worth masking, mapped to placeholder prefixes. GPE/LOC/FAC collapse to one
# LOCATION prefix (legacy behaviour): the distinction between a city, a region and a facility
# tells the model nothing useful once the value is gone.
SPACY_LABELS = {
    "PERSON": "PERSON",
    "ORG": "ORG",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
}


class SpacyNerBackend:
    """Production backend. Prefers `en_core_web_md` (the legacy service's model — better name and
    org recall, which is the limiting factor once masking is global) and falls back to
    `en_core_web_sm`. Parser/tagger/lemmatizer are disabled: only NER is used."""

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
                self._nlp = spacy.load(model, disable=["lemmatizer", "tagger", "parser"])
                self._name = f"spacy:{model}"
                return
            except OSError:
                continue

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return self._nlp is not None

    def entities(self, text: str) -> list[tuple[int, int, str]]:
        if self._nlp is None:
            return []
        doc = self._nlp(text)  # type: ignore[operator]
        return [
            (ent.start_char, ent.end_char, SPACY_LABELS[ent.label_])
            for ent in doc.ents
            if ent.label_ in SPACY_LABELS and ent.text.strip()
        ]


# ---------------------------------------------------------------------------
# Structured patterns — ported verbatim from the legacy service
# ---------------------------------------------------------------------------
#
# Order matters: more specific patterns are listed first so they claim a span before a greedier
# one can (e.g. a patent number before a bare integer).

MASK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Email — first; contains '@', unambiguous.
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),

    # URLs / domains
    ("URL", re.compile(r"\bhttps?://[^\s<>()\"']+", re.IGNORECASE)),
    ("URL", re.compile(r"\bwww\.[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+", re.IGNORECASE)),
    # Bare registrable domain. The TLD allowlist is what stops this firing on "Fig.3", "i.e."
    # and "Brunet & Co." — a bare `\w+\.\w+` here was a false-cancel source.
    ("URL", re.compile(
        r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+"
        r"(?:com|org|net|edu|gov|mil|int|io|co|ai|app|dev|law|biz|info|me|tv|"
        r"us|ca|uk|eu|se|de|fr|nl|jp|cn|kr|au|nz|in|ch|it|es|no|fi|dk|ie|sg|hk)\b",
        re.IGNORECASE,
    )),

    # Street addresses. Requiring capitalized name words avoids "5 Way" / "3 Road map" misfires.
    ("ADDRESS", re.compile(
        r"\b\d{1,6}\s+(?:[A-Z][A-Za-z0-9.'\-]*\s+){1,4}"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|"
        r"Court|Ct|Way|Place|Pl|Square|Sq|Terrace|Ter|Crescent|Cres|Townline|"
        r"Trail|Parkway|Pkwy|Highway|Hwy|Circle|Cir|Close|Row|Walk|Concession)"
        r"\b\.?"
    )),
    ("ADDRESS", re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d{1,6}\b", re.IGNORECASE)),
    # Number-less named street. Distinctive suffixes only (no St/Rd/Way/Walk) to limit idiom
    # misfires; the legacy service accepted the residual false-cancel risk on this high-risk class.
    ("ADDRESS", re.compile(
        r"\b(?:[A-Z][A-Za-z'\-]+\s+){1,3}"
        r"(?:Street|Avenue|Boulevard|Road|Lane|Drive|Townline|Concession|"
        r"Parkway|Highway|Crescent|Terrace)\b"
    )),

    # Postcodes. NOTE: bare 5-digit US ZIP is deliberately NOT matched — it is indistinguishable
    # from ordinary 5-digit numbers (amounts, counts, patent fragments) and masking it was judged
    # too noisy. ZIP+4 is distinctive enough to be safe.
    ("POSTCODE", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")),      # UK
    ("POSTCODE", re.compile(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b")),       # Canada
    ("POSTCODE", re.compile(r"\b\d{5}-\d{4}\b")),                             # US ZIP+4

    # Patent numbers — in an IP practice these are among the most client-identifying tokens
    # in any message, which is why they are masked rather than treated as domain vocabulary.
    ("PATENT", re.compile(r"\bPCT/[A-Z]{2}\d{4}/\d{5,6}\b")),
    ("PATENT", re.compile(r"\bWO[\s/]?\d{4}[/\s]?\d{6}(?:\s?[A-Z]\d?)?\b")),
    ("PATENT", re.compile(r"\bEP[\s]?\d[\d\s]{5,8}\d(?:\s?[A-Z]\d?)?\b")),
    ("PATENT", re.compile(r"\bUS[\s]?\d{4}/\d{7}(?:\s?[A-Z]\d?)?\b")),
    ("PATENT", re.compile(r"\bUS[\s]?[\d,]{7,12}(?:\s?[A-Z]\d?)?\b")),
    ("PATENT", re.compile(r"\bCA[\s]?[\d,]{6,10}(?:\s?[A-Z]\d?)?\b")),

    # Application / docket / file references
    ("APPNO", re.compile(
        r"\b(?:Application|Serial|Appl\.?)\s*(?:No\.?|Number|#)?\s*[:.]?\s*"
        r"\d{2}[/\-]?\d{3},?\d{3}\b",
        re.IGNORECASE,
    )),
    ("DOCKET", re.compile(
        r"\b(?:Docket|Our\s+Ref(?:erence)?|Your\s+Ref(?:erence)?|File)\s*"
        r"(?:No\.?|Number|#)?\s*[:.]?\s*[A-Z0-9][A-Z0-9\-/.]{3,}\b",
        re.IGNORECASE,
    )),
    ("DOCKET", re.compile(r"\b[A-Z]{2,5}-\d{3,5}(?:-\d{2,5})+\b")),
    ("DOCKET", re.compile(r"\b[A-Z]{2,5}-\d{3,6}\b")),

    # Phone — North American, then international.
    ("PHONE", re.compile(
        r"(?<![\w.])(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}\b"
    )),
    ("PHONE", re.compile(
        r"(?<![\w.])\+\d{1,3}(?:[\s.\-]?\(?\d{1,4}\)?){1,2}(?:[\s.\-]\d{2,8}){1,3}\b"
    )),

    ("MONEY", re.compile(
        r"(?:(?:USD|CAD|EUR|GBP|\$|€|£)\s?[\d,]+(?:\.\d{2})?)"
        r"|(?:\b[\d,]+(?:\.\d{2})?\s?(?:USD|CAD|EUR|GBP|dollars|euros)\b)",
        re.IGNORECASE,
    )),

    # Dates — a date plus a matter is often enough to identify a filing.
    ("DATE", re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b")),
    ("DATE", re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    )),
    ("DATE", re.compile(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\.?\s+\d{4}\b",
        re.IGNORECASE,
    )),
    ("DATE", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
)

# The fail-closed backstop set — a STRICT SUBSET of the above, and the distinction is the
# design. These classes cancel the call if they survive into the redacted text. Dates, money,
# patent and docket numbers are masked but absent here on purpose: cancelling on a surviving
# date would ground the drafter permanently for no privacy gain.
GUARD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label.lower(), pattern)
    for label, pattern in MASK_PATTERNS
    if label in {"EMAIL", "URL", "PHONE", "ADDRESS", "POSTCODE"}
)

# ---------------------------------------------------------------------------
# Exclusion sets — values that pass through in cleartext
# ---------------------------------------------------------------------------
#
# Every entry must be genuinely non-identifying. Client-identifying values are NEVER added here;
# they are caught by NER, regex, or the known-entity dictionary.

# IP/patent domain acronyms. Masking "PCT" protects nobody and costs the model the vocabulary
# it needs to draft sensibly.
STOPLIST = frozenset({
    "IP", "PCT", "EP", "EPO", "WIPO", "USPTO", "CIPO", "EUIPO", "INTA",
    "PPH", "OA", "IDS", "RCE", "POA", "NPE", "PTO", "EPC", "TM", "IPR",
    "PTAB", "FTO", "NDA", "WO", "PPA", "NOA", "ROA", "SB", "ADS",
})

# Our own prompt scaffolding. NER false-positives on these would trip the leak guard.
RESERVED = frozenset({
    "PRINCIPAL", "OTHER", "CONVERSATION THREAD", "ATTACHMENTS", "STYLE EXAMPLES",
    "TASK", "EXAMPLE", "ATTACHMENT", "KNOWLEDGE BASE", "EMAIL THREAD",
})

# Generic geography. Applied ONLY to LOCATION entities — a client named "Jordan" or an org
# named "Alberta Holdings" must still mask.
COMMON_GEO = frozenset({
    "us", "u.s.", "usa", "u.s.a.", "united states", "america",
    "canada", "ca", "china", "prc", "japan", "korea", "south korea",
    "north korea", "india", "mexico", "brazil", "australia", "uk",
    "u.k.", "united kingdom", "great britain", "britain", "england",
    "germany", "france", "italy", "spain", "netherlands", "switzerland",
    "sweden", "norway", "denmark", "finland", "ireland", "belgium",
    "austria", "poland", "russia", "taiwan", "hong kong", "singapore",
    "new zealand", "israel", "europe", "eu", "european union", "asia",
    "north america", "south america", "africa", "scandinavia",
    "ontario", "on", "quebec", "qc", "british columbia", "bc",
    "alberta", "ab", "manitoba", "mb", "saskatchewan", "sk",
    "nova scotia", "ns", "new brunswick", "nb", "newfoundland", "nl",
    "prince edward island", "pe", "pei",
    "california", "new york", "texas", "tx", "florida", "fl",
    "washington", "wa", "massachusetts", "ma", "illinois", "il",
    "pennsylvania", "pa", "ohio", "oh", "georgia", "ga", "michigan", "mi",
    "new jersey", "nj", "virginia", "va", "colorado", "arizona", "az",
    "delaware", "de", "minnesota", "mn", "oregon", "or",
})


def _norm(text: str) -> str:
    """Lowercased, edge-punctuation-stripped form for set membership tests."""
    return text.strip().strip(" \t()[]{}<>:;,.\"'`").lower()


def is_excluded(entity_type: str, value: str, allow: Iterable[str] = ()) -> bool:
    """True if ``value`` should pass through in cleartext.

    ``allow`` is the principal-allow set: the firm's own people and entity names. Only FULL forms
    belong in it — listing a bare surname would leave client entities containing that surname
    ("H. Brunet Family Trust") unmasked, which is precisely the case the legacy service guards.
    """
    normalized = _norm(value)
    if not normalized:
        return True
    upper = value.strip().upper()
    # Alpha-only form so "Attachment(s" still matches the RESERVED entry "ATTACHMENTS".
    alpha = re.sub(r"[^A-Za-z]", "", value).upper()
    if upper in STOPLIST or alpha in STOPLIST:
        return True
    if upper in RESERVED or alpha in RESERVED or normalized.upper() in RESERVED:
        return True
    if normalized in {a.strip().lower() for a in allow}:
        return True
    if entity_type == "LOCATION" and normalized in COMMON_GEO:
        return True
    return False


# ---------------------------------------------------------------------------
# Result types + errors
# ---------------------------------------------------------------------------


class RedactionUnavailable(RuntimeError):
    """The NER backend is unavailable and `require_ner` is set — fail closed, do not send."""


class RedactionLeak(RuntimeError):
    """Verification found a high-risk value surviving in the masked text — do not send."""


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


def _occurrence_pattern(value: str) -> re.Pattern[str]:
    """Whole-token matcher: the value not flanked by alphanumerics.

    Not `\\b...\\b` — that fails for values ending in punctuation ("Acme Corp."), because `\\b`
    only asserts a boundary adjacent to a word character.
    """
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])")


def scan_structured_identifiers(text: str) -> list[str]:
    """Independent fail-closed backstop: high-risk identifiers present in ``text``.

    Deliberately independent of any mapping — it asserts a property of the OUTPUT, so it catches
    masker bugs and identifiers no detection pass ever recorded. Placeholders (`PERSON_001`)
    contain no '@', scheme, TLD or qualifying digit run, so they never self-trigger.
    """
    hits: list[str] = []
    for kind, pattern in GUARD_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(f"{kind}:{match.group(0)[:60]}")
    return hits


def redact(
    text: str,
    backend: NerBackend | None = None,
    known_entities: Iterable[str] = (),
    allow_entities: Iterable[str] = (),
    require_ner: bool = True,
) -> RedactionResult:
    """De-identify ``text``, returning the masked text plus the mapping needed to rehydrate.

    Both regex and NER run against the ORIGINAL text, so neither sees the other's placeholders —
    running NER over partially-substituted text fragments names and leaks surnames. Spans are
    collected, overlaps resolved with **regex winning over NER** (it is the more precise layer),
    and only then is masking applied, longest value first.

    ``known_entities`` (client and party names from the OS tables) are masked unconditionally,
    closing the residual where NER simply never tags a known client.
    """
    backend = backend or SpacyNerBackend()
    if not backend.available() and require_ner:
        raise RedactionUnavailable(
            f"NER backend {backend.name!r} unavailable — refusing to redact (D45 fail-closed)"
        )

    # 1. Collect spans: (start, end, type, priority). Lower priority wins an overlap.
    spans: list[tuple[int, int, str, int]] = []
    for entity_type, pattern in MASK_PATTERNS:
        for match in pattern.finditer(text):
            if match.group(0).strip():
                spans.append((match.start(), match.end(), entity_type, 0))
    if backend.available():
        for start, end, label in backend.entities(text):
            spans.append((start, end, label, 1))

    # 2. Resolve overlaps greedily in document order, preferring regex, then longer spans.
    spans.sort(key=lambda s: (s[0], s[3], -(s[1] - s[0])))
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    occupied_end = -1
    for start, end, entity_type, _priority in spans:
        if start < occupied_end:
            continue
        occupied_end = end
        value = text[start:end].strip()
        if not value or is_excluded(entity_type, value, allow_entities):
            continue
        if value not in seen:
            seen.add(value)
            values.append((value, entity_type))

    # 3. Known entities — always masked, regardless of what the detectors found.
    for known in known_entities:
        candidate = known.strip()
        if candidate and candidate not in seen and not is_excluded(
            "CLIENT", candidate, allow_entities
        ):
            seen.add(candidate)
            values.append((candidate, "CLIENT"))

    # 4. Mask every occurrence, longest value first so a short value cannot fragment a longer
    #    one. A placeholder is allocated only when the value is actually present, so a
    #    known-entity absent from this text does not inflate the mapping or the counts.
    values.sort(key=lambda vt: -len(vt[0]))
    masked = text
    mapping: dict[str, str] = {}
    by_value: dict[str, str] = {}
    counters: dict[str, int] = {}
    for value, entity_type in values:
        pattern = _occurrence_pattern(value)
        if not pattern.search(masked):
            continue
        existing = by_value.get(value)
        if existing is None:
            counters[entity_type] = counters.get(entity_type, 0) + 1
            existing = f"{entity_type}_{counters[entity_type]:03d}"
            by_value[value] = existing
            mapping[existing] = value
        masked = pattern.sub(existing, masked)

    result = RedactionResult(
        masked=masked,
        mapping=mapping,
        ref=f"red_{uuid.uuid4().hex}",
        backend=backend.name,
        entity_counts=dict(counters),
        structured_hits=len(values),
    )
    result.leaks = verify_clean(result.masked, result.mapping)
    return result


def verify_clean(masked: str, mapping: dict[str, str]) -> list[str]:
    """Everything that must stop the call, empty meaning clean. Two layers:

    1. **Entity check** — did any mapped value survive as a whole token? Boundary-aware, matching
       the masking pattern exactly, so "Mark" inside "Marketing" is not reported.
    2. **Structured scan** — did any high-risk identifier survive at all, whether or not the
       masker knew about it? This is the true fail-closed property: it fires on masker bugs.
    """
    leaks = [
        value for value in sorted(mapping.values(), key=len, reverse=True)
        if value and _occurrence_pattern(value).search(masked)
    ]
    leaks.extend(scan_structured_identifiers(masked))
    return leaks


def rehydrate(text: str, mapping: dict[str, str]) -> str:
    """Restore original values in the model's response (the inverse of `redact`).

    Longest placeholder first so `PERSON_001` cannot be replaced inside `PERSON_0011`.
    """
    restored = text
    for placeholder in sorted(mapping, key=len, reverse=True):
        restored = restored.replace(placeholder, mapping[placeholder])
    return restored


def ner_available(backend: NerBackend | None = None) -> bool:
    """Whether entity recognition is usable. Ops surfaces this: an unavailable model means every
    LLM-backed agent is failing closed — safe, but silent without a signal."""
    return (backend or SpacyNerBackend()).available()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def record_redaction(conn: psycopg.Connection, agent_name: str, result: RedactionResult) -> str:
    """Persist the audit row and return the reference the egress gate will demand.

    Counts and the leak verdict only — never the mapping. "Did redaction run before this call,
    and did it come back clean?" is answered in full; "what were the real names?" is the thing
    the service exists to withhold.
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

    Bundling the three steps makes the safe path the easy one: a caller needs a redaction ref
    (the egress gate rejects them without it) and cannot obtain one without having passed
    verification.
    """
    result = redact(text, backend=backend, known_entities=known_entities,
                    allow_entities=allow_entities, require_ner=True)
    record_redaction(conn, agent_name, result)
    if result.leaks:
        raise RedactionLeak(
            f"{len(result.leaks)} value(s) survived redaction — refusing egress (ref {result.ref})"
        )
    return result
