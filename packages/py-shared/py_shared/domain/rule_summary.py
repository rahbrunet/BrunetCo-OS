"""Plain-English rule summaries (WP 1.3, M1-R4 dual-mode viewer).

Every docket rule renders two ways: an editable structured form and a plain-English summary
generated FROM that form. This is the summary generator. It is deliberately deterministic (no
LLM) so the same definition always reads the same way; the A2 NL round-trip (WP 6.6) will later
invert this direction, so keep the phrasing regular and parseable.

Example:
  definition = {title: "Respond to Office Action", deadline_type: "extendable_external",
                offsets: {respond_by: {months: 4}, final_due_date: {months: 6}}}
  → "When an office action is received on a CA patent matter, create the extendable-external task
     'Respond to Office Action', due 4 months from the trigger date (final due 6 months from the
     trigger date). This deadline is extendable."
"""
from __future__ import annotations

from typing import Any

_DEADLINE_PHRASE = {
    "hard_external": "hard-external",
    "extendable_external": "extendable-external",
    "internal": "internal",
    "general_reminder": "general-reminder",
    "event": "event",
    "transient_event": "transient-event",
}

_UNIT_ORDER = ("years", "months", "days")
_UNIT_WORD = {"years": "year", "months": "month", "days": "day"}


def _offset_phrase(offset: dict[str, int]) -> str:
    """{'years': 2, 'months': 6} → '2 years 6 months'."""
    parts = []
    for unit in _UNIT_ORDER:
        n = offset.get(unit, 0)
        if n:
            word = _UNIT_WORD[unit]
            parts.append(f"{n} {word}" + ("s" if n != 1 else ""))
    return " ".join(parts) if parts else "0 days"


def _trigger_phrase(trigger_code: str) -> str:
    """Turn a trigger code into readable English. 'task_completed:declaration' → 'the
    "declaration" task is completed'; 'office_action' → 'an office action is received'."""
    if trigger_code.startswith("task_completed:"):
        code = trigger_code.split(":", 1)[1].replace("_", " ")
        return f'the "{code}" task is completed'
    readable = trigger_code.split(":", 1)[0].replace("_", " ")
    return f"{readable} occurs" if readable else "the trigger fires"


def summarize(
    definition: dict[str, Any],
    trigger_code: str,
    jurisdiction_code: str | None = None,
) -> str:
    """Render a docket rule definition as one plain-English sentence (+ notes)."""
    title = definition.get("title", "(untitled)")
    deadline_type = _DEADLINE_PHRASE.get(definition.get("deadline_type", ""), "task")
    scope = f"{jurisdiction_code} " if jurisdiction_code else "any-jurisdiction "

    offsets = definition.get("offsets", {})
    clauses = []
    if "respond_by" in offsets:
        clauses.append(f"due {_offset_phrase(offsets['respond_by'])} from the trigger date")
    if "final_due_date" in offsets:
        verb = "final due" if clauses else "due"
        clauses.append(f"{verb} {_offset_phrase(offsets['final_due_date'])} from the trigger date")
    due_clause = clauses[0] if clauses else "with no offset"
    if len(clauses) > 1:
        due_clause = f"{clauses[0]} ({clauses[1]})"

    sentence = (
        f"When {_trigger_phrase(trigger_code)} on a {scope}matter, create the "
        f"{deadline_type} task '{title}', {due_clause}."
    )

    notes = []
    if definition.get("deadline_type") == "extendable_external":
        notes.append("This deadline is extendable.")
    if "alternate_offsets" in definition:
        alt = definition["alternate_offsets"].get("final_due_date", {})
        notes.append(f"Alternate path: final due {_offset_phrase(alt)} from the trigger date.")
    if definition.get("actions"):
        for a in definition["actions"]:
            if a.get("type") == "set_field":
                notes.append(f"Also sets matter field {a['field']} = {a['expr']}.")
    if definition.get("completion_code"):
        notes.append(f"Completing this task triggers '{definition['completion_code']}'.")
    if definition.get("auto_generate") is False:
        notes.append("Auto-generation is off (created only on demand).")

    return " ".join([sentence, *notes])
