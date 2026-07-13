"""AppColl TaskType → declarative rule mapping (WP 1.3, M1-R3).

Pure, I/O-free: a CSV row in, a mapping result out. The API layer (apps/api/app/routes/rules.py)
does the DB writes; everything here is exhaustively unit-testable and never touches Postgres.

The 552 legacy AppColl task types are the seed of the OS docket rule library. Each row becomes
either:
  * a MappedRule   — a docket_rules.definition in the WP 1.2 declarative form, or
  * Unresolved     — kept for manual resolution (D37: trigger-event linkage is via opaque IDs
                     absent from the export, so a row with no resolvable trigger cannot be
                     auto-mapped and must never be silently dropped), or
  * a LadderStub   — a reminder-pair half that becomes an A18 ladder definition (WP 6.12), not a
                     standalone rule.

Special handling driven by D37:
  * Six deadline types map onto app.deadline_type (counts must reconcile: 151/65/35/28/270/3).
  * USPTO-integration task types are mapped but tagged 'superseded-by-a1' and left INACTIVE — the
    OS watcher framework (WPs 6.2–6.4) replaces them; kept queryable for parallel-run checks.
  * Dual-path rules (20 in the library) carry alternate offsets.
  * Matter-field setter actions ("Update Matter: AllowanceDate={TriggeringTask.RefDate}") become
    definition.actions (M1-R13; the v1 engine stores but does not yet execute them).

CSV schema (documented in apps/api/tests/fixtures/README): one header row, columns —
  task_type_id, name, deadline_type, jurisdiction, trigger_event, auto_generate,
  respond_by_offset, final_due_offset, alternate_offset, owner_resolution, field_action,
  reminder_of, source_integration
Offsets are compact tokens: '6m', '10m', '14d', '2y', or combined '2y6m'. Empty = absent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# AppColl's six deadline-type labels → the app.deadline_type enum (D37 taxonomy).
DEADLINE_TYPE_MAP = {
    "hard external": "hard_external",
    "extendable external": "extendable_external",
    "internal deadline": "internal",
    "internal": "internal",
    "general reminder": "general_reminder",
    "event": "event",
    "transient event": "transient_event",
}

_OFFSET_TOKEN = re.compile(r"(\d+)\s*([ymd])", re.IGNORECASE)
_UNIT = {"y": "years", "m": "months", "d": "days"}


@dataclass
class MappedRule:
    """A successfully mapped rule ready to insert into app.docket_rules."""

    appcoll_task_type_id: str
    name: str
    trigger_code: str
    jurisdiction_code: str | None
    definition: dict[str, Any]
    active: bool
    import_tags: list[str] = field(default_factory=list)


@dataclass
class Unresolved:
    """A row that could not be auto-mapped — kept for manual resolution, never dropped."""

    appcoll_task_type_id: str
    reason: str
    raw: dict[str, str]


@dataclass
class LadderStub:
    """A reminder-pair half → an A18 ladder definition (WP 6.12), not a standalone rule."""

    appcoll_task_type_id: str
    reminder_of: str
    raw: dict[str, str]


ImportResult = MappedRule | Unresolved | LadderStub


def parse_offset(token: str) -> dict[str, int] | None:
    """'2y6m' → {'years': 2, 'months': 6}; '' → None. Unknown text → None (caller decides)."""
    token = (token or "").strip()
    if not token:
        return None
    out: dict[str, int] = {}
    for value, unit in _OFFSET_TOKEN.findall(token):
        out[_UNIT[unit.lower()]] = out.get(_UNIT[unit.lower()], 0) + int(value)
    return out or None


def _parse_field_action(spec: str) -> dict[str, str] | None:
    """'AllowanceDate={TriggeringTask.RefDate}' → a set-field action (M1-R13)."""
    spec = (spec or "").strip()
    if not spec or "=" not in spec:
        return None
    field_name, expr = spec.split("=", 1)
    return {"type": "set_field", "field": field_name.strip(), "expr": expr.strip()}


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y", "t")


def map_row(row: dict[str, str]) -> ImportResult:
    """Map one AppColl TaskType CSV row to an ImportResult. Never raises on data issues —
    anything unmappable becomes Unresolved with a reason."""
    task_id = (row.get("task_type_id") or "").strip()
    name = (row.get("name") or "").strip()
    if not task_id or not name:
        return Unresolved(task_id, "missing task_type_id or name", dict(row))

    # Reminder-pair half → A18 ladder stub, not a rule.
    reminder_of = (row.get("reminder_of") or "").strip()
    if reminder_of:
        return LadderStub(task_id, reminder_of, dict(row))

    # Deadline type must be one of the six.
    dl_raw = (row.get("deadline_type") or "").strip().lower()
    deadline_type = DEADLINE_TYPE_MAP.get(dl_raw)
    if deadline_type is None:
        return Unresolved(task_id, f"unknown deadline_type {row.get('deadline_type')!r}", dict(row))

    # Trigger linkage: opaque IDs are absent from the export (D37). No trigger → unresolvable.
    trigger = (row.get("trigger_event") or "").strip()
    if not trigger:
        return Unresolved(
            task_id, "no resolvable trigger_event (opaque linkage in export)", dict(row)
        )

    # Offsets → dual dates (M1-R11). At least one must be present.
    offsets: dict[str, dict[str, int]] = {}
    respond_by = parse_offset(row.get("respond_by_offset", ""))
    final_due = parse_offset(row.get("final_due_offset", ""))
    if respond_by:
        offsets["respond_by"] = respond_by
    if final_due:
        offsets["final_due_date"] = final_due
    if not offsets:
        return Unresolved(task_id, "no parseable offsets", dict(row))

    definition: dict[str, Any] = {
        "title": name,
        "deadline_type": deadline_type,
        "offsets": offsets,
        "business_day_roll": True,
        "auto_generate": _truthy(row.get("auto_generate", "true")),
    }

    # Dual-path / alternate offset (D37: 20 rules have conditional dual deadlines). Stored for
    # the engine's later alternate-path support; the v1 engine ignores unknown keys.
    alternate = parse_offset(row.get("alternate_offset", ""))
    if alternate:
        definition["alternate_offsets"] = {"final_due_date": alternate}

    # Owner resolution (D37 vocabulary) — carried as data; engine assignment logic is a later WP.
    owner = (row.get("owner_resolution") or "").strip()
    if owner:
        definition["owner_resolution"] = {"raw": owner}

    # Matter-field setter action (M1-R13).
    action = _parse_field_action(row.get("field_action", ""))
    if action:
        definition["actions"] = [action]

    # USPTO-integration task types: mapped but superseded by the A1 watcher framework — inactive.
    tags: list[str] = []
    active = True
    if (row.get("source_integration") or "").strip().upper() == "USPTO":
        tags.append("superseded-by-a1")
        active = False

    jurisdiction = (row.get("jurisdiction") or "").strip() or None

    return MappedRule(
        appcoll_task_type_id=task_id,
        name=name,
        trigger_code=trigger,
        jurisdiction_code=jurisdiction,
        definition=definition,
        active=active,
        import_tags=tags,
    )


@dataclass
class ImportSummary:
    mapped: list[MappedRule] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    ladder_stubs: list[LadderStub] = field(default_factory=list)

    def deadline_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.mapped:
            dt = m.definition["deadline_type"]
            counts[dt] = counts.get(dt, 0) + 1
        return counts

    def as_json(self) -> dict[str, Any]:
        return {
            "mapped": len(self.mapped),
            "unresolved": len(self.unresolved),
            "ladder_stubs": len(self.ladder_stubs),
            "deadline_type_counts": self.deadline_type_counts(),
            "superseded_by_a1": sum(1 for m in self.mapped if "superseded-by-a1" in m.import_tags),
        }


def classify_rows(rows: list[dict[str, str]]) -> ImportSummary:
    """Map every row; bucket into mapped / unresolved / ladder stubs. Every input row lands in
    exactly one bucket — nothing is dropped (acceptance: 552/552 accounted for)."""
    summary = ImportSummary()
    for row in rows:
        result = map_row(row)
        if isinstance(result, MappedRule):
            summary.mapped.append(result)
        elif isinstance(result, LadderStub):
            summary.ladder_stubs.append(result)
        else:
            summary.unresolved.append(result)
    return summary
