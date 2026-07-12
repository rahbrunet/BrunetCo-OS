"""Docketing engine — fire a trigger on a matter, generate tasks + M1-R14 provenance (WP 1.2).

Runs entirely on a caller-supplied RLS-scoped connection (D44): a matter the caller cannot see
yields no rules fired and a LookupError; the task/provenance inserts are policed by Postgres.

Flow (M1-R2): a trigger (event, task completion, watcher, manual) fires on a matter → every
active rule matching the trigger code (and the matter's jurisdiction, when the rule is
jurisdiction-scoped) computes its offsets from the ref date, rolls over the jurisdiction's
holiday calendar, inserts the task, and writes the provenance record in the same transaction.
Chaining: completing a task whose rule declared a ``completion_code`` fires
``task_completed:<code>`` as a new trigger.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg

from py_shared.domain.deadlines import compute_deadlines


@dataclass
class GeneratedTask:
    task_id: UUID
    provenance_id: UUID
    rule_id: UUID
    rule_version: int
    title: str
    respond_by: date | None
    final_due_date: date | None


def load_holidays(conn: psycopg.Connection, jurisdiction_code: str) -> dict[date, str]:
    rows = conn.execute(
        "select holiday_date, name from app.holidays where jurisdiction_code = %s",
        (jurisdiction_code,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def fire_trigger(
    conn: psycopg.Connection,
    matter_id: UUID,
    trigger_code: str,
    ref_date: date,
    trigger_type: str = "event",
    trigger_id: str | None = None,
    generated_by: str = "rule_engine",
) -> list[GeneratedTask]:
    """Fire ``trigger_code`` on a matter as of ``ref_date``; returns the generated tasks.

    Rule selection: active rules whose trigger matches and whose ``jurisdiction_code`` is null
    or equals the matter's, at the **latest version** with ``effective_from <= ref_date`` —
    versioned rules apply as they stood on the trigger date, not as they stand today (M1-R4).
    """
    matter = conn.execute(
        "select family_id, jurisdiction_code from app.matters where id = %s", (matter_id,)
    ).fetchone()
    if matter is None:
        raise LookupError("matter not found or not visible")
    family_id, matter_jurisdiction = matter

    rules = conn.execute(
        """
        select distinct on (rule_id)
               rule_id, version, definition
          from app.docket_rules
         where trigger_code = %s
           and active
           and effective_from <= %s
           and (jurisdiction_code is null or jurisdiction_code = %s)
         order by rule_id, version desc
        """,
        (trigger_code, ref_date, matter_jurisdiction),
    ).fetchall()
    if not rules:
        return []

    holidays = load_holidays(conn, matter_jurisdiction)
    generated: list[GeneratedTask] = []
    for rule_id, version, definition in rules:
        calculated = compute_deadlines(definition, ref_date, holidays)
        respond_by = calculated["respond_by"].rolled if "respond_by" in calculated else None
        final_due = (
            calculated["final_due_date"].rolled if "final_due_date" in calculated else None
        )
        completion_code = definition.get("completion_code")
        task_id = uuid4()
        conn.execute(
            """
            insert into app.tasks
              (id, matter_id, title, deadline_type, ref_date, respond_by, final_due_date,
               generated_by, rule_id, rule_version, trigger_code)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, matter_id, definition["title"], definition["deadline_type"], ref_date,
             respond_by, final_due, generated_by, rule_id, version, completion_code),
        )
        provenance_id = uuid4()
        conn.execute(
            """
            insert into app.task_provenance
              (id, task_id, matter_id, family_id, rule_id, rule_version, trigger_type,
               trigger_id, input_dates, calculated_dates, generated_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (provenance_id, task_id, matter_id, family_id, rule_id, version, trigger_type,
             trigger_id, json.dumps({"ref_date": ref_date.isoformat()}),
             json.dumps({k: v.as_json() for k, v in calculated.items()}), generated_by),
        )
        generated.append(GeneratedTask(
            task_id=task_id, provenance_id=provenance_id, rule_id=rule_id, rule_version=version,
            title=definition["title"], respond_by=respond_by, final_due_date=final_due,
        ))
    return generated


def complete_task(
    conn: psycopg.Connection, task_id: UUID, closed_on: date
) -> tuple[bool, list[GeneratedTask]]:
    """Mark a task completed and fire any chained rules (M1-R2: rules can chain).

    Returns ``(found, chained_tasks)``. Chaining: if the completed task's rule declared a
    ``completion_code``, ``task_completed:<code>`` fires on the same matter with the completion
    date as the new ref date.
    """
    row = conn.execute(
        """
        update app.tasks set status = 'completed', closed_on = %s
         where id = %s and status = 'open'
        returning matter_id, trigger_code
        """,
        (closed_on, task_id),
    ).fetchone()
    if row is None:
        return False, []
    matter_id, completion_code = row
    if not completion_code:
        return True, []
    chained = fire_trigger(
        conn,
        matter_id,
        f"task_completed:{completion_code}",
        closed_on,
        trigger_type="task_completion",
        trigger_id=str(task_id),
    )
    return True, chained


def rule_definition_is_valid(definition: dict[str, Any]) -> str | None:
    """Cheap structural validation of the declarative form; returns an error string or None."""
    if not isinstance(definition.get("title"), str) or not definition["title"]:
        return "definition.title (non-empty string) is required"
    if not isinstance(definition.get("deadline_type"), str):
        return "definition.deadline_type is required"
    offsets = definition.get("offsets")
    if not isinstance(offsets, dict) or not offsets:
        return "definition.offsets must be a non-empty object"
    for key, off in offsets.items():
        if key not in ("respond_by", "final_due_date"):
            return f"unknown offset key {key!r}"
        if not isinstance(off, dict):
            return f"offsets.{key} must be an object"
        for unit in off:
            if unit not in ("years", "months", "days"):
                return f"unknown offset unit {unit!r} in offsets.{key}"
    return None
