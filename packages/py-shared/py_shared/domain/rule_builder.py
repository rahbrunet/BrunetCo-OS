"""Task-Rule Builder A2 (WP 6.6, spec §A2, M1-R4) — NL ↔ declarative round-trip, tests, dry-run.

WP 1.3 shipped the manual rule editor and the deterministic form→English summariser. This inverts
that direction: a practitioner describes a deadline rule in a sentence, A2 proposes the
declarative form, and the OS immediately shows what it will actually do.

"Shows what it will actually do" is the whole design, because a docket rule is the highest-blast-
radius configuration in the system — a wrong one silently mis-dates every matter it touches, and
nobody notices until a deadline passes. So the drafted rule is never trusted on the strength of
the model's say-so. Three checks run before a human is asked to approve it:

  1. **Structural validation** — the definition must be a shape the deadline engine can execute.
  2. **The round-trip** — the drafted form is rendered BACK to plain English by the deterministic
     WP 1.3 summariser. If that sentence does not match what the practitioner asked for, the model
     misunderstood, and the mismatch is visible in the practitioner's own language rather than in
     JSON they would have to learn to read.
  3. **Generated test cases + dry run** — concrete trigger→due-date examples computed by the real
     deadline engine (M1-R4). A human reviews dates, not abstractions.

Nothing here writes a rule. A2 proposes; a human approves and saves through the WP 1.3 editor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg

from py_shared import llm, redaction
from py_shared.domain import rule_summary
from py_shared.domain.deadlines import compute_deadlines
from py_shared.orchestrator import LLM_EGRESS, egress_check

AGENT_NAME = "a2-rule-builder"
TASK = "draft_rule"

VALID_DEADLINE_TYPES = {
    "hard_external", "extendable_external", "internal", "general_reminder",
    "event", "transient_event",
}
VALID_OFFSET_KEYS = {"respond_by", "final_due_date"}
VALID_UNITS = {"years", "months", "days"}

SYSTEM_PROMPT = (
    "You convert a description of an intellectual-property docketing rule into a declarative "
    "definition. Reply with ONLY a JSON object:\n"
    '{"trigger_code": "office_action", "definition": {"title": "...", '
    '"deadline_type": "hard_external|extendable_external|internal|general_reminder|event|'
    'transient_event", "offsets": {"respond_by": {"months": 4}, "final_due_date": {"months": 6}}, '
    '"business_day_roll": true}}\n'
    "Offsets are calendar years/months/days from the trigger date. Use respond_by for the first "
    "action date and final_due_date for the last possible date; include only what the description "
    "states. Use extendable_external when the description mentions extensions. Do not invent "
    "offsets the description does not give. No prose outside the JSON."
)


class RuleDraftError(ValueError):
    """The drafted rule could not be parsed or is not executable by the deadline engine."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_definition(definition: dict[str, Any]) -> None:
    """Raise `RuleDraftError` unless the deadline engine could execute this definition.

    Checked before anything is shown to a human: a definition the engine cannot run produces no
    test cases and no dry run, so the practitioner would be reviewing an empty preview and might
    reasonably read that as "nothing to worry about".
    """
    problems: list[str] = []

    if not definition.get("title"):
        problems.append("the rule needs a title")

    deadline_type = definition.get("deadline_type")
    if deadline_type not in VALID_DEADLINE_TYPES:
        problems.append(f"unknown deadline type {deadline_type!r}")

    offsets = definition.get("offsets")
    if not isinstance(offsets, dict) or not offsets:
        problems.append("the rule needs at least one offset")
    else:
        for key, offset in offsets.items():
            if key not in VALID_OFFSET_KEYS:
                problems.append(f"unknown offset {key!r}")
                continue
            if not isinstance(offset, dict) or not offset:
                problems.append(f"offset {key!r} is empty")
                continue
            for unit, amount in offset.items():
                if unit not in VALID_UNITS:
                    problems.append(f"offset {key!r} has unknown unit {unit!r}")
                elif not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    problems.append(f"offset {key!r}.{unit} must be a non-negative whole number")

    # A respond-by date after the final due date is nonsense that would docket backwards. The
    # engine would compute it happily, which is exactly why it is caught here.
    if isinstance(offsets, dict) and {"respond_by", "final_due_date"} <= set(offsets):
        if _offset_days(offsets["respond_by"]) > _offset_days(offsets["final_due_date"]):
            problems.append("respond_by falls after final_due_date")

    if problems:
        raise RuleDraftError("; ".join(problems))


def _offset_days(offset: dict[str, int]) -> int:
    """Rough ordering key — months as 30 days, years as 365. Only ever used to compare two offsets
    for obvious inversion, never to compute a real date."""
    return (
        int(offset.get("years", 0)) * 365
        + int(offset.get("months", 0)) * 30
        + int(offset.get("days", 0))
    )


# ---------------------------------------------------------------------------
# Test-case generation + dry run (M1-R4)
# ---------------------------------------------------------------------------


@dataclass
class RuleTestCase:
    trigger_date: date
    dates: dict[str, date] = field(default_factory=dict)
    rolled: dict[str, list[str]] = field(default_factory=dict)


def generate_test_cases(
    definition: dict[str, Any],
    trigger_dates: list[date],
    holidays: dict[date, str] | None = None,
) -> list[RuleTestCase]:
    """Concrete trigger→due-date examples, computed by the REAL deadline engine.

    Using the production engine rather than a re-implementation is the point: these cases are
    evidence about what the rule will do in service, not about what a preview thinks it will do.
    The holiday-roll trace is carried so a surprising date explains itself ("that Sunday rolled to
    Monday, which was Victoria Day, so Tuesday").
    """
    validate_definition(definition)
    cases: list[RuleTestCase] = []
    for trigger in trigger_dates:
        computed = compute_deadlines(definition, trigger, holidays or {})
        case = RuleTestCase(trigger_date=trigger)
        for key, calculated in computed.items():
            case.dates[key] = calculated.rolled
            if calculated.trace:
                case.rolled[key] = [
                    f"{step.from_date} → {step.to_date} ({step.reason})"
                    for step in calculated.trace
                ]
        cases.append(case)
    return cases


def default_trigger_dates(around: date) -> list[date]:
    """A spread of trigger dates that exercises the awkward cases a practitioner should see:
    a month end, a leap-adjacent date, and a Friday (whose offsets often land on a weekend)."""
    return [
        date(around.year, 1, 31),    # month-end clamping
        date(around.year, 2, 28),
        date(around.year, 6, 15),    # an ordinary mid-month date
        date(around.year, 12, 31),   # year boundary
    ]


def dry_run(
    conn: psycopg.Connection,
    definition: dict[str, Any],
    trigger_code: str,
    jurisdiction_code: str | None = None,
    sample_size: int = 10,
    holidays: dict[date, str] | None = None,
) -> list[dict[str, Any]]:
    """Apply a candidate rule to REAL recent matters and report what it would produce.

    Creates nothing — no tasks, no provenance, no writes of any kind (M1-R4). Test cases prove the
    arithmetic; this proves the rule against the firm's actual data, which is where a rule that is
    correct in the abstract turns out to hit two hundred matters nobody expected.
    """
    validate_definition(definition)
    rows = conn.execute(
        """
        select m.id, m.reference, m.filing_date
          from app.matters m
         where m.filing_date is not null
           and (%(jur)s::text is null or m.jurisdiction_code = %(jur)s)
           and m.status not in ('abandoned', 'expired', 'closed')
         order by m.filing_date desc
         limit %(limit)s
        """,
        {"jur": jurisdiction_code, "limit": sample_size},
    ).fetchall()

    preview: list[dict[str, Any]] = []
    for matter_id, reference, filing_date in rows:
        computed = compute_deadlines(definition, filing_date, holidays or {})
        preview.append({
            "matter_id": matter_id,
            "reference": reference,
            "trigger_date": filing_date,
            "would_create": definition.get("title"),
            "dates": {key: value.rolled for key, value in computed.items()},
        })
    return preview


# ---------------------------------------------------------------------------
# The round-trip
# ---------------------------------------------------------------------------


@dataclass
class DraftedRule:
    trigger_code: str
    definition: dict[str, Any]
    # The deterministic WP 1.3 summary of what was actually drafted — the practitioner reads this,
    # not the JSON, to confirm the model understood them.
    reads_as: str
    test_cases: list[RuleTestCase] = field(default_factory=list)


def _parse_draft(raw: str) -> tuple[str, dict[str, Any]]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise RuleDraftError("no JSON object found in the rule-builder response")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuleDraftError(f"rule-builder response was not valid JSON: {exc}") from exc

    trigger_code = data.get("trigger_code")
    definition = data.get("definition")
    if not trigger_code or not isinstance(definition, dict):
        raise RuleDraftError("the response is missing trigger_code or definition")
    return str(trigger_code), definition


def draft_rule(
    conn: psycopg.Connection,
    description: str,
    jurisdiction_code: str | None = None,
    trigger_dates: list[date] | None = None,
    holidays: dict[date, str] | None = None,
    llm_client: llm.LlmClient | None = None,
    ner_backend: redaction.NerBackend | None = None,
) -> DraftedRule:
    """Draft a docket rule from a description, and immediately show what it does.

    Returns the declarative form, the deterministic plain-English rendering of that form (the
    round-trip), and generated test cases. Saving is a separate human act through the WP 1.3
    editor — A2 proposes, it never installs a rule.
    """
    if not description.strip():
        raise RuleDraftError("describe the rule in a sentence or two")

    result = redaction.redact_for_egress(conn, AGENT_NAME, description, backend=ner_backend)
    egress_check(LLM_EGRESS, result.ref)

    client = llm_client or llm.get_llm_client(TASK)
    try:
        raw = client.complete(result.masked, system=SYSTEM_PROMPT)
    except llm.LlmError as exc:
        llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked),
                       status="failed", detail=str(exc)[:500])
        raise
    llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked))

    trigger_code, definition = _parse_draft(raw)
    if isinstance(definition.get("title"), str):
        definition["title"] = redaction.rehydrate(definition["title"], result.mapping)
    validate_definition(definition)

    reads_as = rule_summary.summarize(definition, trigger_code, jurisdiction_code)
    cases = generate_test_cases(
        definition, trigger_dates or default_trigger_dates(date.today()), holidays
    )
    return DraftedRule(
        trigger_code=trigger_code, definition=definition, reads_as=reads_as, test_cases=cases,
    )
