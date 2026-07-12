"""Daily docket digest (M1-R5, WP 1.4) — per-user due/overdue summary → email.send events.

Runs as a system worker (D44 enumerated exception, registered in DECISIONS.md): the digest must
see every user's assigned tasks regardless of who triggers the run, so it reads on the worker's
system connection, not a user JWT. Output goes to the `email.send` queue; the transport adapter
is a logging stub until the Graph/Outlook bridge lands (Phase 4/6 — sending needs Entra creds).

Digest contents per active user: their open tasks that are overdue or due within the horizon
(default 7 days), ordered by effective due date (respond_by, else final_due_date).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

DEFAULT_HORIZON_DAYS = 7


@dataclass
class Digest:
    user_id: str
    email: str
    display_name: str
    subject: str
    body: str
    task_count: int


def build_daily_digests(
    conn: psycopg.Connection, as_of: date, horizon_days: int = DEFAULT_HORIZON_DAYS
) -> list[Digest]:
    """Compose one digest per active user with due/overdue open tasks. Pure read — no sends."""
    rows = conn.execute(
        """
        select u.id, u.email, u.display_name,
               m.reference, t.title, t.respond_by, t.final_due_date,
               coalesce(t.respond_by, t.final_due_date) as effective_due
          from app.tasks t
          join app.matters m on m.id = t.matter_id
          join app.os_users u on u.id = t.assignee_id
         where u.is_active
           and t.status = 'open'
           and coalesce(t.respond_by, t.final_due_date)
               <= %(as_of)s + make_interval(days => %(horizon)s)
         order by u.id, effective_due
        """,
        {"as_of": as_of, "horizon": horizon_days},
    ).fetchall()

    by_user: dict[str, list[tuple[Any, ...]]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for r in rows:
        uid = str(r[0])
        by_user.setdefault(uid, []).append(r)
        meta[uid] = (r[1], r[2])

    digests: list[Digest] = []
    for uid, tasks in by_user.items():
        email, name = meta[uid]
        overdue = [t for t in tasks if t[7] < as_of]
        upcoming = [t for t in tasks if t[7] >= as_of]
        lines = [f"Daily docket for {name} — {as_of.isoformat()}", ""]
        if overdue:
            lines.append(f"OVERDUE ({len(overdue)}):")
            lines += [f"  {t[7].isoformat()}  {t[3]}  {t[4]}" for t in overdue]
            lines.append("")
        if upcoming:
            lines.append(f"Due within {DEFAULT_HORIZON_DAYS} days ({len(upcoming)}):")
            lines += [f"  {t[7].isoformat()}  {t[3]}  {t[4]}" for t in upcoming]
        subject = f"Docket {as_of.isoformat()}: {len(overdue)} overdue, {len(upcoming)} upcoming"
        digests.append(Digest(
            user_id=uid, email=email, display_name=name, subject=subject,
            body="\n".join(lines), task_count=len(tasks),
        ))
    return digests


def handle_daily_digest(payload: dict[str, Any]) -> None:
    """`docket.daily_digest` handler: build digests, enqueue one email.send event per user.

    Payload: {"as_of": "YYYY-MM-DD" (optional, default today), "horizon_days": int (optional)}.
    """
    from py_shared.config import settings

    as_of = date.fromisoformat(payload["as_of"]) if payload.get("as_of") else date.today()
    horizon = int(payload.get("horizon_days", DEFAULT_HORIZON_DAYS))
    with psycopg.connect(settings.supabase_db_url) as conn:
        digests = build_daily_digests(conn, as_of, horizon)
        for d in digests:
            conn.execute(
                "insert into ops.events (type, payload) values ('email.send', %s)",
                (json.dumps({"to": d.email, "subject": d.subject, "body": d.body}),),
            )
        conn.commit()
    print(f"[worker] docket.daily_digest: {len(digests)} digests queued for {as_of}")


def handle_email_send(payload: dict[str, Any]) -> None:
    """`email.send` transport stub — logs only.

    TODO(Phase 4/6): replace with the Graph/Outlook send adapter once Entra application
    credentials exist. Events stay in ops.events either way, so queued mail is replayable.
    """
    print(f"[worker] email.send (stub): to={payload.get('to')!r} "
          f"subject={payload.get('subject')!r}")
