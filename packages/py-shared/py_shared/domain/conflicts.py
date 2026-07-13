"""Conflict checks (WP 5B.4, D34) — run a firm-wide party/mark search and log the result.

The search itself is app.search_conflicts (a SECURITY DEFINER function that sees the whole firm,
including restricted families — the conflict that matters most is the one you can't otherwise see).
This module runs it on the caller's connection, records the run in app.conflict_checks (so a
cleared conflict is provable), and returns the matches.

normalize_query is a small pure helper (trimming / whitespace collapse) exposed for unit testing
and reused by callers that want to dedupe or pre-clean a query.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg


@dataclass
class ConflictMatch:
    kind: str          # client | contact | family | matter
    ref: str
    label: str
    matched_on: str
    score: float

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "label": self.label,
                "matched_on": self.matched_on, "score": round(self.score, 4)}


def normalize_query(q: str) -> str:
    """Trim and collapse internal whitespace. Empty/blank raises (a conflict check needs a term)."""
    cleaned = re.sub(r"\s+", " ", (q or "").strip())
    if not cleaned:
        raise ValueError("conflict query is empty")
    return cleaned


def search_conflicts(
    conn: psycopg.Connection, query: str, min_score: float = 0.3
) -> list[ConflictMatch]:
    """Run the firm-wide conflict search (definer function) and return ranked matches."""
    q = normalize_query(query)
    rows = conn.execute(
        "select kind, ref, label, matched_on, score from app.search_conflicts(%s, %s::real)",
        (q, min_score),
    ).fetchall()
    return [ConflictMatch(r[0], r[1], r[2], r[3], float(r[4])) for r in rows]


def run_and_log_check(
    conn: psycopg.Connection, query: str, run_by: str,
    check_type: str = "on_demand", matter_id: UUID | None = None,
    min_score: float = 0.3,
) -> tuple[UUID, list[ConflictMatch]]:
    """Run a conflict search and record it in app.conflict_checks (D38: every check is logged).
    Returns (check_id, matches)."""
    matches = search_conflicts(conn, query, min_score)
    results = [m.as_json() for m in matches]
    row = conn.execute(
        """
        insert into app.conflict_checks
          (query_text, check_type, matter_id, result_count, results, run_by)
        values (%s, %s, %s, %s, %s, %s) returning id
        """,
        (normalize_query(query), check_type, matter_id, len(matches),
         json.dumps(results), run_by),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0])), matches
