"""Board framework — typed columns, membership, and the no-code automation engine
(WP 5.3, spec §M9, D30).

A board is a lens over work items, not a container. Three concerns live here:

  * **Typed column values** — custom columns carry values that must validate against the column's
    type before they are stored, because a board that lets "estimated pages" hold the string
    "next Tuesday" is a board whose kanban and workload views quietly break.

  * **Scope-based membership** — which items a board shows is derived from its scope (firm /
    matter / project), never a stored membership list that drifts from the items themselves.

  * **The automation engine** — the substantive part. A rule is a trigger plus ordered actions.
    The matcher is pure and exhaustively testable; the executor runs each action and logs the run,
    so a surprising side effect ("why did an invoice-review task appear?") traces to the rule that
    caused it. Automations fire only when a caller reports an event — there is no hidden trigger —
    which keeps the control flow legible and the blast radius bounded.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

# ---------------------------------------------------------------------------
# Typed column values
# ---------------------------------------------------------------------------

TEXT, NUMBER, DATE, SINGLE_SELECT, STATUS, PERSON, CHECKBOX = (
    "text", "number", "date", "single_select", "status", "person", "checkbox"
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class FieldValueInvalid(ValueError):
    """A value does not fit its column's type. Raised before storage, never after — a board that
    tolerates a mistyped value is a board whose typed views misrender it silently."""


def validate_field_value(col_type: str, value: Any, config: dict[str, Any] | None = None) -> None:
    """Raise `FieldValueInvalid` unless ``value`` fits ``col_type``. None (clearing a cell) is
    always allowed — an empty cell is a legitimate state, not a type error."""
    if value is None:
        return
    config = config or {}

    if col_type == TEXT:
        if not isinstance(value, str):
            raise FieldValueInvalid("text column expects a string")
    elif col_type == NUMBER:
        # bool is a subclass of int in Python; a checkbox value in a number column is a real error.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FieldValueInvalid("number column expects a number")
    elif col_type == DATE:
        if not isinstance(value, str):
            raise FieldValueInvalid("date column expects an ISO date string")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise FieldValueInvalid(f"invalid date {value!r}") from exc
    elif col_type in (SINGLE_SELECT, STATUS):
        options = {o.get("value") for o in config.get("options", [])}
        if value not in options:
            raise FieldValueInvalid(
                f"{value!r} is not an allowed option for this {col_type} column"
            )
    elif col_type == PERSON:
        if not (isinstance(value, str) and _UUID_RE.match(value)):
            raise FieldValueInvalid("person column expects a user id")
    elif col_type == CHECKBOX:
        if not isinstance(value, bool):
            raise FieldValueInvalid("checkbox column expects a boolean")
    else:  # pragma: no cover — the enum constrains this at the DB layer
        raise FieldValueInvalid(f"unknown column type {col_type!r}")


def set_field_value(
    conn: psycopg.Connection, work_item_id: UUID, column_id: UUID, value: Any,
) -> None:
    """Upsert a custom column value after validating it against the column's declared type."""
    row = conn.execute(
        "select col_type::text, config, is_builtin from app.board_columns where id = %s",
        (column_id,),
    ).fetchone()
    if row is None:
        raise LookupError("column not found or not visible")
    col_type, config, is_builtin = row
    if is_builtin:
        raise ValueError("builtin columns read through to the work item; they hold no stored value")
    validate_field_value(col_type, value, config)
    conn.execute(
        """
        insert into app.work_item_field_values (work_item_id, column_id, value, updated_at)
        values (%s, %s, %s, now())
        on conflict (work_item_id, column_id)
        do update set value = excluded.value, updated_at = now()
        """,
        (work_item_id, column_id, json.dumps(value)),
    )


# ---------------------------------------------------------------------------
# Scope-based membership
# ---------------------------------------------------------------------------


def board_work_item_ids(conn: psycopg.Connection, board_id: UUID) -> list[UUID]:
    """The work items a board shows, derived from its scope. RLS still applies on top — a
    matter-scoped board a caller cannot fully see returns only the items they can."""
    board = conn.execute(
        "select scope_type::text, scope_id from app.boards where id = %s", (board_id,)
    ).fetchone()
    if board is None:
        raise LookupError("board not found or not visible")
    scope_type, scope_id = board

    if scope_type == "firm":
        rows = conn.execute("select id from app.work_items").fetchall()
    elif scope_type == "matter":
        rows = conn.execute(
            "select id from app.work_items where matter_id = %s", (scope_id,)
        ).fetchall()
    else:  # project
        rows = conn.execute(
            "select id from app.work_items where project_id = %s", (scope_id,)
        ).fetchall()
    return [UUID(str(r[0])) for r in rows]


# ---------------------------------------------------------------------------
# Automation engine
# ---------------------------------------------------------------------------

# Triggers
EVENT_STATUS_CHANGED = "status_changed"
EVENT_FIELD_CHANGED = "field_changed"
_TRIGGER_EVENTS = {EVENT_STATUS_CHANGED, EVENT_FIELD_CHANGED}

# Actions
ACTION_CREATE_TASK = "create_task"
ACTION_NOTIFY = "notify"
ACTION_SET_STATUS = "set_status"
_ACTION_TYPES = {ACTION_CREATE_TASK, ACTION_NOTIFY, ACTION_SET_STATUS}


class AutomationInvalid(ValueError):
    """A trigger or action is malformed. Reported at save time so a board never carries a rule
    that will throw when it fires — the worst time to discover a bad automation."""


def validate_automation(trigger: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    event = trigger.get("event")
    if event not in _TRIGGER_EVENTS:
        raise AutomationInvalid(f"unknown trigger event {event!r}")
    if event == EVENT_FIELD_CHANGED and not trigger.get("column"):
        raise AutomationInvalid("field_changed trigger needs a 'column'")

    if not actions:
        raise AutomationInvalid("automation has no actions")
    for action in actions:
        atype = action.get("type")
        if atype not in _ACTION_TYPES:
            raise AutomationInvalid(f"unknown action type {atype!r}")
        if atype == ACTION_CREATE_TASK and not action.get("title"):
            raise AutomationInvalid("create_task action needs a 'title'")
        if atype == ACTION_NOTIFY and action.get("target") not in ("owner", "creator"):
            raise AutomationInvalid("notify action needs target 'owner' or 'creator'")
        if atype == ACTION_SET_STATUS and not action.get("to"):
            raise AutomationInvalid("set_status action needs a 'to'")


def match_trigger(trigger: dict[str, Any], event: dict[str, Any]) -> bool:
    """Whether an event fires a trigger. Pure — the entire matching contract, exhaustively
    testable without a database.

    A trigger with no target value ("any status change") matches every event of its type; a
    trigger with a target ("-> done") matches only when the event lands on it. field_changed also
    requires the column to match, so a rule on one column ignores edits to another.
    """
    if trigger.get("event") != event.get("type"):
        return False
    if event["type"] == EVENT_STATUS_CHANGED:
        target = trigger.get("to")
        return target is None or event.get("to") == target
    if event["type"] == EVENT_FIELD_CHANGED:
        if trigger.get("column") != event.get("column"):
            return False
        target = trigger.get("to")
        return target is None or event.get("to") == target
    return False


@dataclass
class ActionOutcome:
    action_type: str
    detail: dict[str, Any] = field(default_factory=dict)


def _execute_action(
    conn: psycopg.Connection, action: dict[str, Any], work_item: dict[str, Any],
) -> ActionOutcome:
    atype = action["type"]

    if atype == ACTION_CREATE_TASK:
        # The new task inherits the triggering item's matter/project context, so an automation
        # spawns work on the same file rather than an orphan. Role routing reuses the 5.2 engine.
        assignee: UUID | None = None
        role = action.get("role")
        if role:
            from py_shared.domain import assignment
            assignee = assignment.best_assignee(conn, role)
        row = conn.execute(
            """
            insert into app.work_items
              (title, matter_id, project_id, assignee_id, status, created_by, role)
            values (%s, %s, %s, %s, 'open', %s, %s)
            returning id
            """,
            (action["title"], work_item["matter_id"], work_item["project_id"], assignee,
             work_item["created_by"], role),
        ).fetchone()
        assert row is not None
        return ActionOutcome(atype, {"created_work_item": str(row[0])})

    if atype == ACTION_NOTIFY:
        # No delivery channel yet (Teams is WP 5.4). The intent is recorded on the run so it is
        # auditable now and wire-up later is a change in one place.
        target = action["target"]
        recipient = work_item["assignee_id"] if target == "owner" else work_item["created_by"]
        return ActionOutcome(
            atype, {"notify": target, "recipient": str(recipient) if recipient else None}
        )

    if atype == ACTION_SET_STATUS:
        conn.execute(
            "update app.work_items set status = %s where id = %s",
            (action["to"], work_item["id"]),
        )
        return ActionOutcome(atype, {"set_status": action["to"]})

    raise AutomationInvalid(f"unknown action type {atype!r}")  # pragma: no cover


def run_automations(
    conn: psycopg.Connection, board_id: UUID, work_item_id: UUID, event: dict[str, Any],
) -> list[UUID]:
    """Fire a board's automations for one work-item event; return the run-log ids.

    Every enabled automation is matched; matching ones execute and are logged with what they did,
    non-matching ones are not logged (the log is a record of actions taken, not of every rule
    considered). A failing action records the failure on the run and moves on rather than aborting
    the batch — one broken rule must not silence the others.
    """
    item = conn.execute(
        "select id, matter_id, project_id, assignee_id, created_by from app.work_items "
        " where id = %s",
        (work_item_id,),
    ).fetchone()
    if item is None:
        raise LookupError("work item not found or not visible")
    work_item = {
        "id": item[0], "matter_id": item[1], "project_id": item[2],
        "assignee_id": item[3], "created_by": item[4],
    }

    automations = conn.execute(
        "select id, trigger, actions from app.board_automations "
        " where board_id = %s and enabled order by created_at",
        (board_id,),
    ).fetchall()

    run_ids: list[UUID] = []
    for automation_id, trigger, actions in automations:
        if not match_trigger(trigger, event):
            continue
        taken: list[dict[str, Any]] = []
        detail: str | None = None
        try:
            for action in actions:
                outcome = _execute_action(conn, action, work_item)
                taken.append({"type": outcome.action_type, **outcome.detail})
        except Exception as exc:  # noqa: BLE001 — record and continue; one bad rule is not fatal
            detail = f"action failed: {exc}"
        run = conn.execute(
            """
            insert into app.board_automation_runs
              (automation_id, work_item_id, matched, actions_taken, detail)
            values (%s, %s, true, %s, %s) returning id
            """,
            (automation_id, work_item_id, json.dumps(taken), detail),
        ).fetchone()
        assert run is not None
        run_ids.append(UUID(str(run[0])))
    return run_ids
