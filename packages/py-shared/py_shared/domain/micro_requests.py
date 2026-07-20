"""Micro-requests (WP 5.4, spec §M9, D30) — the email-ping-pong replacement.

A micro-request is an intra-day "@request" on a work item or document: a prompt, an owner, an SLA,
and a thread. The state machine is deliberately small — open → answered → (re-open | resolved) —
because the whole value proposition is that a review round-trip stays lightweight and on the item
instead of scattering across inboxes.

The one hard rule: an open request BLOCKS its parent work item (enforced in
`app.work_item_is_blocked`, migration 0021), so a task cannot be completed while a review of it is
outstanding. That is the guarantee email never gave — "I sent it for review" and "it was actually
reviewed" become the same fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import psycopg

DEFAULT_SLA_HOURS = 4  # an intra-day request; the point is same-day turnaround


@dataclass
class MicroRequest:
    id: UUID
    status: str
    assignee_id: UUID
    requester_id: UUID


def create_request(
    conn: psycopg.Connection,
    requester_id: UUID,
    assignee_id: UUID,
    prompt: str,
    parent_work_item_id: UUID | None = None,
    parent_document_id: UUID | None = None,
    sla_hours: float | None = DEFAULT_SLA_HOURS,
    now: datetime | None = None,
) -> UUID:
    """Raise a request against exactly one parent. Immediately blocks a work-item parent.

    A request assigned to oneself is allowed (a self-reminder to review before filing), but the
    prompt must be non-empty — an empty @request is the digital equivalent of a blank sticky note.
    """
    if (parent_work_item_id is None) == (parent_document_id is None):
        raise ValueError("a micro-request must hang off exactly one parent (item xor document)")
    if not prompt.strip():
        raise ValueError("a micro-request needs a prompt")

    sla_due = None
    if sla_hours is not None:
        base = now or datetime.now().astimezone()
        sla_due = base + timedelta(hours=sla_hours)

    row = conn.execute(
        """
        insert into app.micro_requests
          (parent_work_item_id, parent_document_id, requester_id, assignee_id, prompt, sla_due)
        values (%s, %s, %s, %s, %s, %s) returning id
        """,
        (parent_work_item_id, parent_document_id, requester_id, assignee_id, prompt, sla_due),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def post_message(
    conn: psycopg.Connection, request_id: UUID, author_id: UUID, body: str,
) -> str:
    """Add a message and advance the state by WHO spoke:

      * the assignee replying moves open → answered (the ball is back with the requester);
      * the requester replying to an answered request re-opens it (another round-trip);
      * a message on a resolved request is refused — resolution is a deliberate close, and
        reopening is an explicit action, not a side effect of a stray comment.

    Returns the resulting status. Unlimited rounds: nothing caps how many times this ping-pongs,
    which is the feature — the thread lives here instead of an inbox.
    """
    row = conn.execute(
        "select status::text, requester_id, assignee_id from app.micro_requests where id = %s",
        (request_id,),
    ).fetchone()
    if row is None:
        raise LookupError("micro-request not found or not visible")
    status, requester_id, assignee_id = row
    if status == "resolved":
        raise ValueError("cannot post to a resolved request; re-raise a new one")
    if not body.strip():
        raise ValueError("message body is empty")

    conn.execute(
        "insert into app.micro_request_messages (request_id, author_id, body) values (%s, %s, %s)",
        (request_id, author_id, body),
    )

    new_status: str = status
    if author_id == UUID(str(assignee_id)) and status == "open":
        new_status = "answered"
    elif author_id == UUID(str(requester_id)) and status == "answered":
        new_status = "open"
    if new_status != status:
        conn.execute(
            "update app.micro_requests set status = %s::app.micro_request_status where id = %s",
            (new_status, request_id),
        )
    return new_status


def resolve_request(conn: psycopg.Connection, request_id: UUID, resolved_by: UUID) -> None:
    """Close a request, unblocking its parent. Only an unresolved request can be resolved.

    Resolving is what lifts the parent block — so the completion guard on the parent work item and
    this call are two ends of the same guarantee: the task frees up exactly when the review is
    declared done, no sooner.
    """
    updated = conn.execute(
        """
        update app.micro_requests
           set status = 'resolved', resolved_at = now(), resolved_by = %s
         where id = %s and status <> 'resolved'
         returning parent_work_item_id
        """,
        (resolved_by, request_id),
    ).fetchone()
    if updated is None:
        raise LookupError("micro-request not found, not visible, or already resolved")


def open_requests_for(conn: psycopg.Connection, assignee_id: UUID) -> list[MicroRequest]:
    """A person's outstanding requests — the intra-day queue feeding My Day (WP 5.5)."""
    rows = conn.execute(
        "select id, status::text, assignee_id, requester_id from app.micro_requests "
        " where assignee_id = %s and status <> 'resolved' order by sla_due nulls last",
        (assignee_id,),
    ).fetchall()
    return [MicroRequest(id=r[0], status=r[1], assignee_id=r[2], requester_id=r[3]) for r in rows]


def turnaround_stats(conn: psycopg.Connection, assignee_id: UUID) -> dict[str, float | int]:
    """Resolved-request turnaround for one person: count, average hours, on-time rate.

    The measurable is framed as the person's own (spec §M9: seat-owned, not surveillance) — it
    surfaces on their dashboard first. On-time rate is the share resolved on or before the SLA.
    """
    row = conn.execute(
        """
        select
          count(*) filter (where resolved_at is not null),
          avg(turnaround_hours) filter (where resolved_at is not null),
          count(*) filter (where sla_outcome = 'on_time'),
          count(*) filter (where sla_outcome in ('on_time', 'late'))
        from app.micro_request_turnaround
        where assignee_id = %s
        """,
        (assignee_id,),
    ).fetchone()
    assert row is not None
    resolved, avg_hours, on_time, decided = row
    return {
        "resolved": resolved or 0,
        "avg_turnaround_hours": float(avg_hours) if avg_hours is not None else 0.0,
        "on_time_rate": (on_time / decided) if decided else 0.0,
    }
