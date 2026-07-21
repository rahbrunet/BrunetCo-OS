"""A18 reminder & follow-up ladders (WP 6.12, spec §A18, D31) — the escalating chase engine.

Three vocabulary words, used consistently:

  * **ladder** — configuration: an escalating sequence of rungs for a (kind, task type,
    jurisdiction). The canonical maintenance-fee ladder is T−60 courtesy → T−30 action requested →
    T−14 "FINAL REMINDER"; every offset, count and wording is data, so a different task type or
    jurisdiction is a different set of rows, never a different branch here.
  * **schedule** — one live run of a ladder against one task or one work item.
  * **send** — one rendered rung of one schedule.

The two safety properties this module exists to guarantee:

  1. **Silence never abandons (D31).** :func:`sweep_reminders` never closes a schedule quietly. A
     ladder that runs out of rungs with no response becomes 'exhausted' and immediately opens an
     escalation carrying a *pending* decision, which a human has to record. Rights-preserving
     ladders make that an explicit pay-or-abandon call. The pending-escalation set is the report
     that proves nothing lapsed by default.
  2. **Review-first sending (D31, revised).** Nothing here sends. Every rung is rendered and
     handed to the WP 6.1 approval queue as a `reminder.send` proposal; a human approves it, and
     only then does the registered handler mark it sent. `auto_remind` is preserved but dormant
     (default false, per client and per task type) and — even when on — never covers AI-composed
     content. Unsubscribe always wins over both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from py_shared.orchestrator import ACTION_HANDLERS, propose_action

AGENT_NAME = "a18-reminder"
SEND_ACTION = "reminder.send"

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class LadderConfigError(ValueError):
    """A ladder definition that would misbehave at run time (bad signs, bad ordering, no rungs)."""


@dataclass(frozen=True)
class Rung:
    """One configured step of a ladder."""

    step_no: int
    offset_days: int
    label: str
    subject: str
    body: str


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------

def render_template(template: str, context: dict[str, object]) -> str:
    """Substitute ``{placeholder}`` tokens, refusing anything the context cannot fill.

    Strict on purpose: a reminder that goes out saying "your {jurisdiction} deadline" is worse than
    one that never renders, because the second failure is visible and the first goes to a client.
    """
    missing = sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)} - set(context))
    if missing:
        raise LadderConfigError(f"template references unknown placeholders: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda m: str(context[m.group(1)]), template)


def validate_rungs(kind: str, rungs: list[Rung]) -> None:
    """Reject ladder shapes the engine cannot honour.

    Deadline ladders count backward (all offsets negative), follow-ups count forward (all
    positive), and in both cases offsets must strictly increase with step number — that ordering is
    what makes "later rung = more escalated" true, which the catch-up rule in :func:`due_rungs`
    depends on.
    """
    if not rungs:
        raise LadderConfigError("a ladder needs at least one rung")
    ordered = sorted(rungs, key=lambda r: r.step_no)
    for rung in ordered:
        if kind == "deadline" and rung.offset_days >= 0:
            raise LadderConfigError(
                f"deadline ladder rung {rung.step_no} must be before the deadline "
                f"(negative offset), got {rung.offset_days}"
            )
        if kind == "awaiting_client" and rung.offset_days <= 0:
            raise LadderConfigError(
                f"awaiting-client rung {rung.step_no} must be after the tag "
                f"(positive offset), got {rung.offset_days}"
            )
    offsets = [r.offset_days for r in ordered]
    if any(b <= a for a, b in zip(offsets, offsets[1:], strict=False)):
        raise LadderConfigError(f"rung offsets must strictly increase, got {offsets}")


def rung_due_date(anchor: date, rung: Rung) -> date:
    from datetime import timedelta

    return anchor + timedelta(days=rung.offset_days)


def due_rungs(
    anchor: date, rungs: list[Rung], today: date, already_sent: set[int],
) -> tuple[Rung | None, list[Rung]]:
    """Split the outstanding rungs into (the one to send now, the ones it supersedes).

    When the sweep has been running normally exactly one rung comes due, and this returns it with
    an empty superseded list. When it has *not* run for a while, several rungs are due at once —
    firing all of them would land three escalating emails in a client's inbox in one minute. So the
    most-escalated due rung is the one that goes out and the earlier ones are recorded as
    superseded. This is deliberately biased toward escalation: a missed sweep can cost the client a
    courtesy notice, never the FINAL REMINDER.
    """
    pending = [r for r in sorted(rungs, key=lambda r: r.step_no) if r.step_no not in already_sent]
    due = [r for r in pending if rung_due_date(anchor, r) <= today]
    if not due:
        return None, []
    return due[-1], due[:-1]


def is_exhausted(anchor: date, rungs: list[Rung], today: date, already_sent: set[int]) -> bool:
    """True once every rung has been accounted for and the last one's date has passed.

    This is the trigger for the escalation, not for a close — see the module docstring.
    """
    if not rungs:
        return False
    if not all(r.step_no in already_sent for r in rungs):
        return False
    last = max(rungs, key=lambda r: r.step_no)
    return rung_due_date(anchor, last) <= today


def resolve_send_mode(
    auto_remind: bool, unsubscribed: bool, ai_composed: bool,
) -> str:
    """Decide how a rendered rung leaves the system: 'suppress', 'auto', or 'review'.

    Order matters and encodes D31:

      * an unsubscribed client is never chased, whatever else is configured;
      * AI-composed content (A9 answering a client's question) is reviewed regardless of the flag;
      * `auto_remind` — dormant, default false — is the only path that skips review, and it covers
        deterministic template content only.
    """
    if unsubscribed:
        return "suppress"
    if ai_composed or not auto_remind:
        return "review"
    return "auto"


# ---------------------------------------------------------------------------
# Ladder configuration (DB)
# ---------------------------------------------------------------------------

def load_rungs(conn: psycopg.Connection, ladder_id: UUID) -> list[Rung]:
    rows = conn.execute(
        "select step_no, offset_days, label, subject, body from app.reminder_ladder_steps "
        " where ladder_id = %s order by step_no",
        (ladder_id,),
    ).fetchall()
    return [Rung(step_no=r[0], offset_days=r[1], label=r[2], subject=r[3], body=r[4]) for r in rows]


def save_ladder(
    conn: psycopg.Connection,
    kind: str,
    name: str,
    task_type: str,
    rungs: list[Rung],
    created_by: UUID,
    jurisdiction_code: str | None = None,
    rights_preserving: bool = False,
) -> UUID:
    """Create a ladder and its rungs, validating the shape before anything is stored."""
    if kind not in ("deadline", "awaiting_client"):
        raise LadderConfigError(f"unknown ladder kind {kind!r}")
    validate_rungs(kind, rungs)
    row = conn.execute(
        """
        insert into app.reminder_ladders
          (kind, name, task_type, jurisdiction_code, rights_preserving, created_by)
        values (%s::app.reminder_ladder_kind, %s, %s, %s, %s, %s) returning id
        """,
        (kind, name, task_type, jurisdiction_code, rights_preserving, created_by),
    ).fetchone()
    assert row is not None
    ladder_id = UUID(str(row[0]))
    for rung in rungs:
        conn.execute(
            "insert into app.reminder_ladder_steps "
            " (ladder_id, step_no, offset_days, label, subject, body) values (%s,%s,%s,%s,%s,%s)",
            (ladder_id, rung.step_no, rung.offset_days, rung.label, rung.subject, rung.body),
        )
    return ladder_id


def match_ladder(
    conn: psycopg.Connection, kind: str, task_type: str, jurisdiction_code: str | None,
) -> UUID | None:
    """Most specific active ladder for the combination: jurisdiction-specific beats the fallback."""
    row = conn.execute(
        """
        select id from app.reminder_ladders
         where active and kind = %s::app.reminder_ladder_kind and task_type = %s
           and (jurisdiction_code = %s or jurisdiction_code is null)
         order by (jurisdiction_code is null)   -- false (specific) sorts first
         limit 1
        """,
        (kind, task_type, jurisdiction_code),
    ).fetchone()
    return UUID(str(row[0])) if row else None


# ---------------------------------------------------------------------------
# Starting and halting schedules
# ---------------------------------------------------------------------------

def start_ladder_for_task(conn: psycopg.Connection, task_id: UUID) -> UUID | None:
    """Start the matching deadline ladder for a task. Returns None when nothing matches.

    Silent no-ops are correct here: a task with no type, no due date, or no configured ladder is
    simply not chased by A18, and the caller (rule engine or docket UI) should not have to care.
    An already-chased task is also a no-op — the partial unique index makes double-chasing
    impossible, and this checks first so a re-run does not raise.
    """
    row = conn.execute(
        """
        select t.task_type, t.final_due_date, t.matter_id, m.jurisdiction_code
          from app.tasks t join app.matters m on m.id = t.matter_id
         where t.id = %s and t.status = 'open'
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    task_type, due, matter_id, jurisdiction = row
    if not task_type or due is None:
        return None
    if conn.execute(
        "select 1 from app.reminder_schedules where task_id = %s and status = 'active'",
        (task_id,),
    ).fetchone():
        return None

    ladder_id = match_ladder(conn, "deadline", task_type, jurisdiction)
    if ladder_id is None:
        return None
    created = conn.execute(
        "insert into app.reminder_schedules (ladder_id, matter_id, task_id, anchor_date) "
        "values (%s, %s, %s, %s) returning id",
        (ladder_id, matter_id, task_id, due),
    ).fetchone()
    assert created is not None
    return UUID(str(created[0]))


def start_follow_up(
    conn: psycopg.Connection, work_item_id: UUID, task_type: str, tagged_on: date,
) -> UUID | None:
    """Start an awaiting-client follow-up ladder on a work item (spec §A18(b))."""
    row = conn.execute(
        """
        select w.matter_id, m.jurisdiction_code
          from app.work_items w join app.matters m on m.id = w.matter_id
         where w.id = %s and w.status <> 'done'
        """,
        (work_item_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    matter_id, jurisdiction = row
    if conn.execute(
        "select 1 from app.reminder_schedules where work_item_id = %s and status = 'active'",
        (work_item_id,),
    ).fetchone():
        return None

    ladder_id = match_ladder(conn, "awaiting_client", task_type, jurisdiction)
    if ladder_id is None:
        return None
    created = conn.execute(
        "insert into app.reminder_schedules (ladder_id, matter_id, work_item_id, anchor_date) "
        "values (%s, %s, %s, %s) returning id",
        (ladder_id, matter_id, work_item_id, tagged_on),
    ).fetchone()
    assert created is not None
    return UUID(str(created[0]))


def halt_schedule(conn: psycopg.Connection, schedule_id: UUID, reason: str) -> None:
    """Stop chasing, on the record. The reason is required and is never overwritten.

    Called when an instruction arrives — A8 detecting a reply, or someone logging it manually —
    and the halt is what feeds the instruction back into the annuity/task workflow.
    """
    if not reason.strip():
        raise ValueError("halting a ladder requires a reason")
    updated = conn.execute(
        "update app.reminder_schedules set status = 'halted', halted_reason = %s, "
        " halted_at = now() where id = %s and status = 'active' returning id",
        (reason, schedule_id),
    ).fetchone()
    if updated is None:
        raise LookupError("schedule not found, not visible, or no longer active")


def halt_for_task(conn: psycopg.Connection, task_id: UUID, reason: str) -> int:
    """Halt whatever ladder is chasing this task. The A8 reply-detection entry point.

    Returns the number of schedules halted (0 or 1) so a caller can distinguish "stopped chasing"
    from "was not chasing".
    """
    rows = conn.execute(
        "update app.reminder_schedules set status = 'halted', halted_reason = %s, "
        " halted_at = now() where task_id = %s and status = 'active' returning id",
        (reason, task_id),
    ).fetchall()
    return len(rows)


def cancel_schedule(conn: psycopg.Connection, schedule_id: UUID) -> None:
    """Stand a ladder down without recording an instruction (the task closed, or an admin says so).

    Distinct from a halt because it makes no claim that the client responded.
    """
    conn.execute(
        "update app.reminder_schedules set status = 'cancelled' "
        " where id = %s and status = 'active'",
        (schedule_id,),
    )


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    queued: int = 0
    suppressed: int = 0
    superseded: int = 0
    exhausted: int = 0
    escalated: int = 0
    cancelled: int = 0


def _render_context(conn: psycopg.Connection, schedule_id: UUID, rung: Rung) -> dict[str, object]:
    row = conn.execute(
        """
        select m.reference, m.jurisdiction_code, c.name, s.anchor_date,
               coalesce(t.title, w.title)
          from app.reminder_schedules s
          join app.matters m   on m.id = s.matter_id
          join app.families f  on f.id = m.family_id
          join app.clients c   on c.id = f.client_id
          left join app.tasks t      on t.id = s.task_id
          left join app.work_items w on w.id = s.work_item_id
         where s.id = %s
        """,
        (schedule_id,),
    ).fetchone()
    assert row is not None, "schedule vanished mid-sweep"
    reference, jurisdiction, client_name, anchor, title = row
    return {
        "matter_reference": reference,
        "jurisdiction": jurisdiction,
        "client_name": client_name,
        "due_date": anchor.isoformat(),
        "task_title": title or "",
        "label": rung.label,
    }


def _suppression_for(conn: psycopg.Connection, schedule_id: UUID) -> tuple[bool, bool]:
    """(auto_remind, unsubscribed) for this schedule's client, task-type row beating the default."""
    row = conn.execute(
        """
        select p.auto_remind, p.unsubscribed
          from app.reminder_schedules s
          join app.matters m  on m.id = s.matter_id
          join app.families f on f.id = m.family_id
          join app.reminder_ladders l on l.id = s.ladder_id
          join app.client_reminder_prefs p
            on p.client_id = f.client_id and p.task_type in (l.task_type, '')
         where s.id = %s
         order by (p.task_type = '')   -- the specific row sorts before the client-wide default
         limit 1
        """,
        (schedule_id,),
    ).fetchone()
    return (bool(row[0]), bool(row[1])) if row else (False, False)


def _escalate(conn: psycopg.Connection, schedule_id: UUID) -> None:
    """Open the pending decision that exhaustion demands. Idempotent (one row per schedule).

    The target is whoever owns the subject; a null target is allowed rather than fatal, because an
    unowned pending decision still shows up in the pending list — losing the escalation entirely to
    a missing assignee is exactly the silent failure D31 forbids.
    """
    owner = conn.execute(
        """
        select coalesce(t.assignee_id, w.assignee_id)
          from app.reminder_schedules s
          left join app.tasks t      on t.id = s.task_id
          left join app.work_items w on w.id = s.work_item_id
         where s.id = %s
        """,
        (schedule_id,),
    ).fetchone()
    conn.execute(
        "insert into app.reminder_escalations (schedule_id, escalated_to) values (%s, %s) "
        " on conflict (schedule_id) do nothing",
        (schedule_id, owner[0] if owner else None),
    )
    conn.execute(
        "update app.reminder_schedules set status = 'escalated' where id = %s",
        (schedule_id,),
    )


def sweep_reminders(conn: psycopg.Connection, today: date | None = None) -> SweepResult:
    """Advance every active schedule by one tick. Idempotent: safe to run twice in a day.

    Per schedule, in order: stand down if the subject is closed → queue the most escalated due rung
    (recording any it supersedes) → escalate if the ladder is now exhausted. Rendering happens here
    rather than at send time so the human reviewing the proposal sees exactly what would go out.
    """
    today = today or date.today()
    result = SweepResult()

    schedules = conn.execute(
        """
        select s.id, s.ladder_id, s.anchor_date, s.matter_id, s.task_id, s.work_item_id,
               l.rights_preserving
          from app.reminder_schedules s
          join app.reminder_ladders l on l.id = s.ladder_id
         where s.status = 'active'
         order by s.created_at
        """,
    ).fetchall()

    for sched_id, ladder_id, anchor, matter_id, task_id, work_item_id, _rights in schedules:
        schedule_id = UUID(str(sched_id))
        if _subject_closed(conn, task_id, work_item_id):
            cancel_schedule(conn, schedule_id)
            result.cancelled += 1
            continue

        rungs = load_rungs(conn, UUID(str(ladder_id)))
        sent = {
            r[0] for r in conn.execute(
                "select step_no from app.reminder_sends where schedule_id = %s", (schedule_id,),
            ).fetchall()
        }
        to_send, superseded = due_rungs(anchor, rungs, today, sent)

        auto_remind, unsubscribed = _suppression_for(conn, schedule_id)
        for rung in superseded:
            _record_suppressed(conn, schedule_id, rung, anchor, "superseded by a later rung")
            result.superseded += 1

        if to_send is not None:
            mode = resolve_send_mode(auto_remind, unsubscribed, ai_composed=False)
            if mode == "suppress":
                _record_suppressed(conn, schedule_id, to_send, anchor, "client unsubscribed")
                result.suppressed += 1
            else:
                _queue(conn, schedule_id, to_send, anchor, matter_id, review=(mode == "review"))
                result.queued += 1
            sent.add(to_send.step_no)
            sent.update(r.step_no for r in superseded)

        if is_exhausted(anchor, rungs, today, sent):
            conn.execute(
                "update app.reminder_schedules set status = 'exhausted' where id = %s",
                (schedule_id,),
            )
            result.exhausted += 1
            # Unconditional: (a) rights-preserving deadlines demand a pay-or-abandon call, and
            # (b) an ordinary follow-up that goes unanswered escalates rather than closing. Same
            # principle, both paths — nothing exhausts into silence.
            _escalate(conn, schedule_id)
            result.escalated += 1

    return result


def _subject_closed(
    conn: psycopg.Connection, task_id: UUID | None, work_item_id: UUID | None,
) -> bool:
    if task_id is not None:
        row = conn.execute(
            "select status::text from app.tasks where id = %s", (task_id,),
        ).fetchone()
        return row is None or row[0] != "open"
    row = conn.execute(
        "select status from app.work_items where id = %s", (work_item_id,),
    ).fetchone()
    return row is None or row[0] == "done"


def _record_suppressed(
    conn: psycopg.Connection, schedule_id: UUID, rung: Rung, anchor: date, reason: str,
) -> None:
    """Write the rung that did not go out. A suppressed rung is still a row: the correspondence
    timeline has to show what was withheld and why, not just what was sent."""
    ctx = _render_context(conn, schedule_id, rung)
    conn.execute(
        """
        insert into app.reminder_sends
          (schedule_id, step_no, due_on, status, subject, body, review_required,
           suppressed_reason)
        values (%s, %s, %s, 'suppressed', %s, %s, false, %s)
        on conflict (schedule_id, step_no) do nothing
        """,
        (
            schedule_id, rung.step_no, rung_due_date(anchor, rung),
            render_template(rung.subject, ctx), render_template(rung.body, ctx), reason,
        ),
    )


def _queue(
    conn: psycopg.Connection, schedule_id: UUID, rung: Rung, anchor: date,
    matter_id: UUID, review: bool,
) -> None:
    ctx = _render_context(conn, schedule_id, rung)
    subject = render_template(rung.subject, ctx)
    body = render_template(rung.body, ctx)
    action_id = propose_action(
        conn, AGENT_NAME, SEND_ACTION,
        payload={
            "schedule_id": str(schedule_id),
            "step_no": rung.step_no,
            "label": rung.label,
            "subject": subject,
            "body": body,
        },
        matter_id=matter_id,
    )
    conn.execute(
        """
        insert into app.reminder_sends
          (schedule_id, step_no, due_on, status, proposed_action_id, subject, body,
           review_required)
        values (%s, %s, %s, 'queued', %s, %s, %s, %s)
        on conflict (schedule_id, step_no) do nothing
        """,
        (
            schedule_id, rung.step_no, rung_due_date(anchor, rung), action_id, subject, body,
            review,
        ),
    )


# ---------------------------------------------------------------------------
# Approved-send handler (WP 6.1 action registry)
# ---------------------------------------------------------------------------

def handle_approved_send(conn: psycopg.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an approved `reminder.send`: mark the rung sent and record its delivery state.

    Transport is not wired yet — the Graph send lands with WP 4.3 — so delivery is recorded as
    'pending_transport' rather than claiming a delivery this system has not made. The approval,
    the exact rendered content and the timestamp are all already durable at this point, which is
    what the correspondence timeline needs; the transport hand-off updates delivery_status later.
    """
    schedule_id = UUID(str(payload["schedule_id"]))
    step_no = int(payload["step_no"])
    updated = conn.execute(
        """
        update app.reminder_sends
           set status = 'sent', sent_at = now(), delivery_status = 'pending_transport'
         where schedule_id = %s and step_no = %s and status = 'queued'
         returning id
        """,
        (schedule_id, step_no),
    ).fetchone()
    if updated is None:
        raise ValueError("no queued reminder send matches this approval")
    return {"send_id": str(updated[0]), "delivery_status": "pending_transport"}


# Registered on import. The API imports this module through its route, so any process that can
# approve an action can also execute one.
ACTION_HANDLERS.setdefault(SEND_ACTION, handle_approved_send)


# ---------------------------------------------------------------------------
# Escalation decisions
# ---------------------------------------------------------------------------

def record_decision(
    conn: psycopg.Connection, escalation_id: UUID, decision: str, decided_by: UUID,
    note: str | None = None,
) -> None:
    """Record the pay-or-abandon call an exhausted ladder demands.

    'pending' is not a decision — it is the absence of one, and cannot be written back.
    """
    if decision not in ("pay", "abandon", "other"):
        raise ValueError(f"not a decision: {decision!r}")
    updated = conn.execute(
        """
        update app.reminder_escalations
           set decision = %s::app.reminder_decision, decided_by = %s, decided_at = now(), note = %s
         where id = %s and decision = 'pending'
         returning id
        """,
        (decision, decided_by, note, escalation_id),
    ).fetchone()
    if updated is None:
        raise LookupError("escalation not found, not visible, or already decided")


def pending_escalations(conn: psycopg.Connection) -> list[dict[str, object]]:
    """Every exhausted ladder still awaiting a human decision — the "nothing lapsed" report."""
    rows = conn.execute(
        """
        select e.id, e.schedule_id, e.escalated_to, e.created_at, m.reference,
               l.rights_preserving, l.name
          from app.reminder_escalations e
          join app.reminder_schedules s on s.id = e.schedule_id
          join app.reminder_ladders l   on l.id = s.ladder_id
          join app.matters m           on m.id = s.matter_id
         where e.decision = 'pending'
         order by l.rights_preserving desc, e.created_at
        """,
    ).fetchall()
    return [
        {
            "id": r[0], "schedule_id": r[1], "escalated_to": r[2], "created_at": r[3],
            "matter_reference": r[4], "rights_preserving": r[5], "ladder_name": r[6],
        }
        for r in rows
    ]
