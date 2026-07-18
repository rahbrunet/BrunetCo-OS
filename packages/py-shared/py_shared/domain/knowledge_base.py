"""Firm Knowledge Base service (WP 6.8, spec §12.1) — grounding with citations.

One retrieval service shared by A3, A4, A9, A11 and the orchestrator. The corpus is practice
manuals, statutes, classification manuals, office practice notices, the firm's own site, and an
allow-listed set of third-party commentary.

Three behaviours here are load-bearing, and each exists because the alternative is a specific
professional harm:

  * **Citations travel with passages.** A retrieved paragraph with no citation invites an agent
    to state law without attribution. Every `Passage` carries the citation a professional would
    actually write, so a drafted letter says "MOPOP §17.02 provides…" or says nothing.

  * **Superseded editions are flagged, never silently served.** Practice manuals are revised and
    an old passage can be flatly wrong. Superseded editions stay queryable — deleting them would
    destroy the firm's ability to explain advice it gave in 2023 — but retrieval excludes them by
    default and marks them loudly when they are requested.

  * **Third-party material is grounding-only.** §12.1 curates external sources deliberately, for
    copyright hygiene: retrieved third-party content is *cited*, not reproduced. `Passage.extract`
    is truncated for those sources and `may_quote` is False, so the constraint reaches the caller
    as data rather than as a paragraph in a design document nobody re-reads.

Retrieval is Postgres FTS (§13: "Postgres FTS in v1, pgvector available for semantic retrieval").
The `search` SQL function ranks; this module shapes results and enforces the licence guard.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import psycopg

# Chunk sizing. Large enough to carry a complete thought (a manual subsection is typically
# 800-2000 chars), small enough that several fit in a prompt alongside the thread being answered.
TARGET_CHUNK_CHARS = 1_800
MAX_CHUNK_CHARS = 2_600
CHUNK_OVERLAP_CHARS = 200

# How much of a grounding-only source may be surfaced. Enough for an agent to understand and
# paraphrase the point; not enough to function as a copy of the work.
GROUNDING_ONLY_EXTRACT_CHARS = 400

LICENSE_OPEN = "open"
LICENSE_FIRM = "firm_owned"
LICENSE_GROUNDING_ONLY = "grounding_only"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    citation: str
    heading_path: str
    ordinal: int
    body: str

    @property
    def char_count(self) -> int:
        return len(self.body)


# Section headings in the corpus this service targets. Ordered most-specific first so
# "MOPOP §17.02.01" is not truncated to "17.02" by a greedier pattern.
_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numbered manual sections: "17.02.01 Double patenting", "§ 17.02 Unity"
    re.compile(r"^\s*§?\s*(\d+(?:\.\d+){1,3})\s+(.{3,120})$"),
    # Statute sections: "Section 28.2 Novelty", "35 U.S.C. 102 Conditions"
    re.compile(r"^\s*(?:Section|Sec\.|§)\s*(\d+(?:\.\d+)?)\s+(.{3,120})$", re.IGNORECASE),
    # CFR/rule form: "Rule 53(b) Continuations"
    re.compile(r"^\s*(?:Rule)\s+(\d+(?:\([a-z]\))?)\s+(.{3,120})$", re.IGNORECASE),
)


def _match_heading(line: str) -> tuple[str, str] | None:
    """(section_number, title) if the line reads as a section heading."""
    if len(line) > 160:
        return None  # a long line is prose that happens to start with a number
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(line.rstrip())
        if match:
            return match.group(1), match.group(2).strip()
    return None


def chunk_document(text: str, citation_prefix: str) -> list[Chunk]:
    """Split a document into citation-anchored chunks.

    Splits on section headings rather than at a fixed character count, because the citation is
    the point: a chunk that spans the boundary between §17.02 and §17.03 cannot be attributed to
    either, and an agent citing it would be wrong about where the law came from.

    Long sections are further split at paragraph boundaries with a small overlap, so a sentence
    spanning the split is not lost to both halves. Every piece keeps its parent section's
    citation.
    """
    lines = text.splitlines()
    sections: list[tuple[str, str, list[str]]] = []  # (citation, heading_path, body lines)
    current_citation = citation_prefix
    current_heading = ""
    current_body: list[str] = []

    for line in lines:
        heading = _match_heading(line)
        if heading is not None:
            if current_body:
                sections.append((current_citation, current_heading, current_body))
                current_body = []
            number, title = heading
            current_citation = f"{citation_prefix} §{number}"
            current_heading = title
        else:
            current_body.append(line)
    if current_body:
        sections.append((current_citation, current_heading, current_body))

    chunks: list[Chunk] = []
    ordinal = 0
    for citation, heading_path, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        for piece in _split_long(body):
            chunks.append(Chunk(citation, heading_path, ordinal, piece))
            ordinal += 1
    return chunks


def _split_long(body: str) -> list[str]:
    """Split an over-long section at paragraph boundaries, with overlap."""
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]

    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > TARGET_CHUNK_CHARS and current:
            pieces.append(current)
            # Carry the tail forward so a thought spanning the boundary survives in one piece.
            current = (current[-CHUNK_OVERLAP_CHARS:] + "\n\n" + paragraph).strip()
        else:
            current = candidate
    if current:
        pieces.append(current)

    # A single paragraph longer than the max still has to be broken somewhere.
    final: list[str] = []
    for piece in pieces:
        while len(piece) > MAX_CHUNK_CHARS:
            final.append(piece[:MAX_CHUNK_CHARS])
            piece = piece[MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS:]
        if piece:
            final.append(piece)
    return final


def content_hash(text: str) -> str:
    """Stable hash making re-ingestion idempotent — an unchanged document is skipped rather than
    re-chunked, which would churn chunk ids and orphan citations held elsewhere."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    jurisdiction: str
    license_class: str
    edition_label: str
    refreshed_at: date | None
    refresh_interval_days: int | None
    superseded_at: date | None = None


def is_stale(source: Source, today: date) -> bool:
    """Whether a source is overdue for refresh.

    A source with no interval (a statute edition) never goes stale on a schedule. A source with
    an interval that has never been refreshed is stale by definition — "we registered the CIPO
    practice-notice feed and never actually fetched it" must read as stale, not as fresh.
    """
    if source.refresh_interval_days is None:
        return False
    if source.refreshed_at is None:
        return True
    return source.refreshed_at + timedelta(days=source.refresh_interval_days) < today


def stale_sources(sources: list[Source], today: date) -> list[Source]:
    """Sources needing attention, worst-overdue first — the ops view of KB health."""
    overdue = [s for s in sources if is_stale(s, today)]
    return sorted(overdue, key=lambda s: (s.refreshed_at or date.min))


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    """One retrieved passage, carrying everything a caller needs to use it responsibly."""

    chunk_id: UUID
    citation: str
    heading_path: str
    extract: str
    source_key: str
    source_name: str
    jurisdiction: str
    license_class: str
    edition_label: str
    is_superseded: bool
    rank: float

    @property
    def may_quote(self) -> bool:
        """False for third-party sources: cite and paraphrase, never reproduce (§12.1).

        Exposed as a property on the passage rather than checked at the call site, so a consumer
        cannot obtain the text without also having the constraint in hand.
        """
        return self.license_class != LICENSE_GROUNDING_ONLY

    def as_prompt_snippet(self) -> str:
        """Render for an LLM prompt: citation first, then the extract, with the superseded
        warning inline where the model cannot miss it."""
        header = f"[{self.citation} — {self.source_name}, {self.edition_label}]"
        if self.is_superseded:
            header += " ⚠ SUPERSEDED EDITION — verify against the current edition before relying"
        if not self.may_quote:
            header += " (third-party: paraphrase and cite, do not quote)"
        return f"{header}\n{self.extract}"


def _apply_license_guard(body: str, license_class: str) -> str:
    """Truncate third-party material to a grounding-sized extract.

    Applied at retrieval, not at render, so no consumer can reach the untruncated body by
    skipping a formatting helper.
    """
    if license_class != LICENSE_GROUNDING_ONLY:
        return body
    if len(body) <= GROUNDING_ONLY_EXTRACT_CHARS:
        return body
    return body[:GROUNDING_ONLY_EXTRACT_CHARS].rstrip() + " […]"


def search(
    conn: psycopg.Connection,
    query: str,
    jurisdictions: list[str] | None = None,
    limit: int = 8,
    include_superseded: bool = False,
) -> list[Passage]:
    """Citation-aware retrieval over the KB.

    Superseded editions are excluded unless asked for: an agent posing a plain question should
    get current law and must opt in to history rather than receive it by accident.
    """
    if not query.strip():
        return []
    rows = conn.execute(
        "select chunk_id, citation, heading_path, body, source_key, source_name, jurisdiction, "
        "       license_class::text, edition_label, is_superseded, rank "
        "  from kb.search(%s, %s, %s, %s)",
        (query, jurisdictions, limit, include_superseded),
    ).fetchall()
    return [
        Passage(
            chunk_id=r[0], citation=r[1], heading_path=r[2] or "",
            extract=_apply_license_guard(r[3], r[7]),
            source_key=r[4], source_name=r[5], jurisdiction=r[6], license_class=r[7],
            edition_label=r[8], is_superseded=r[9], rank=float(r[10]),
        )
        for r in rows
    ]


def grounding_snippets(
    conn: psycopg.Connection,
    query: str,
    jurisdictions: list[str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Prompt-ready snippets for an LLM consumer (A9's `kb_snippets`, A11's report sections).

    Current editions only — an agent drafting client correspondence should never be grounded on
    superseded practice without a human explicitly asking for it.
    """
    return [
        passage.as_prompt_snippet()
        for passage in search(conn, query, jurisdictions, limit, include_superseded=False)
    ]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def ingest_document(
    conn: psycopg.Connection,
    source_id: UUID,
    title: str,
    text: str,
    citation_prefix: str,
    url: str | None = None,
) -> tuple[UUID | None, int]:
    """Chunk and store one document. Returns (document_id, chunks_written).

    Idempotent on content: an unchanged document short-circuits to (existing_id, 0) rather than
    re-chunking. Re-ingesting the whole corpus on a schedule is therefore cheap and safe, which
    is what makes a scheduled refresh viable at all.
    """
    digest = content_hash(text)
    existing = conn.execute(
        "select id from kb.documents where source_id = %s and content_hash = %s",
        (source_id, digest),
    ).fetchone()
    if existing is not None:
        return UUID(str(existing[0])), 0

    row = conn.execute(
        "insert into kb.documents (source_id, title, url, content_hash) "
        "values (%s, %s, %s, %s) returning id",
        (source_id, title, url, digest),
    ).fetchone()
    assert row is not None
    document_id = UUID(str(row[0]))

    chunks = chunk_document(text, citation_prefix)
    for chunk in chunks:
        conn.execute(
            "insert into kb.chunks "
            "  (document_id, source_id, citation, heading_path, ordinal, body, char_count) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (document_id, source_id, chunk.citation, chunk.heading_path, chunk.ordinal,
             chunk.body, chunk.char_count),
        )
    return document_id, len(chunks)


def supersede_edition(
    conn: psycopg.Connection, source_key: str, on_date: date,
) -> None:
    """Mark the current edition of a source superseded, freeing the key for a new edition.

    The old rows stay: they are how the firm explains advice given while that edition was in
    force. Retrieval flags rather than hides them.
    """
    conn.execute(
        "update kb.sources set superseded_at = %s "
        " where key = %s and superseded_at is null",
        (on_date, source_key),
    )
