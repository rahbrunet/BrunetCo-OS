"""A18 ladders against Postgres (WP 6.12, spec §A18, D31).

The two tests that carry the design promise:

  * `test_an_exhausted_ladder_escalates_instead_of_closing` — silence never abandons.
  * `test_the_sweep_never_sends_it_queues` — review-first sending; nothing leaves without a human.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import reminders as rem

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"
DEADLINE = date(2026, 12, 1)


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    """A real user-JWT connection (D44) — RLS applies, unlike the superuser `su` fixture."""
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.reminder_ladders')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.12 migration (0026) not applied")

MAINTENANCE = [
    rem.Rung(1, -60, "courtesy", "{matter_reference}: fee due {due_date}", "Courtesy notice."),
    rem.Rung(2, -30, "action requested", "{matter_reference}: instructions needed", "Please."),
    rem.Rung(3, -14, "FINAL REMINDER", "FINAL REMINDER — {matter_reference}",
             "Absent instructions the fee will not be paid and the application will be abandoned."),
]


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def matter(su: psycopg.Connection) -> Iterator[dict[str, str]]:
    """A client → family → matter chain, torn down child-first (every FK up the chain is
    NO ACTION)."""
    tag = uuid.uuid4().hex[:8]
    client = su.execute(
        "insert into app.clients (code, name) values (%s, %s) returning id",
        (f"T{tag}", f"Test Client {tag}"),
    ).fetchone()[0]
    family = su.execute(
        "insert into app.families (client_id, family_seq, family_type, reference, title) "
        "values (%s, %s, 'patent', %s, 'Widget') returning id",
        (client, 1, f"{tag}"),
    ).fetchone()[0]
    matter_id = su.execute(
        """
        insert into app.matters
          (family_id, reference, jurisdiction_code, jurisdiction_segment, status)
        values (%s, %s, 'CA', 'CA', 'filed') returning id
        """,
        (family, f"{tag}CA"),
    ).fetchone()[0]
    yield {"client": str(client), "family": str(family), "matter": str(matter_id),
           "reference": f"{tag}CA"}

    su.execute("delete from app.reminder_sends where schedule_id in "
               " (select id from app.reminder_schedules where matter_id = %s)", (matter_id,))
    su.execute("delete from app.reminder_escalations where schedule_id in "
               " (select id from app.reminder_schedules where matter_id = %s)", (matter_id,))
    su.execute("delete from app.reminder_schedules where matter_id = %s", (matter_id,))
    su.execute("delete from app.proposed_actions where matter_id = %s", (matter_id,))
    su.execute("delete from app.tasks where matter_id = %s", (matter_id,))
    su.execute("delete from app.work_items where matter_id = %s", (matter_id,))
    su.execute("delete from app.client_reminder_prefs where client_id = %s", (client,))
    su.execute("delete from app.matters where id = %s", (matter_id,))
    su.execute("delete from app.families where id = %s", (family,))
    su.execute("delete from app.clients where id = %s", (client,))


def _purge_ladder(su: psycopg.Connection, lid: uuid.UUID) -> None:
    """Drop a ladder and everything hanging off it, child-first (every FK here is NO ACTION)."""
    su.execute("delete from app.reminder_sends where schedule_id in "
               " (select id from app.reminder_schedules where ladder_id = %s)", (lid,))
    su.execute("delete from app.reminder_escalations where schedule_id in "
               " (select id from app.reminder_schedules where ladder_id = %s)", (lid,))
    su.execute("delete from app.reminder_schedules where ladder_id = %s", (lid,))
    su.execute("delete from app.reminder_ladders where id = %s", (lid,))


@pytest.fixture()
def ladder(su: psycopg.Connection) -> Iterator[uuid.UUID]:
    lid = rem.save_ladder(
        su, "deadline", f"CA maintenance fee {uuid.uuid4().hex[:6]}",
        task_type=f"maintenance_fee_{uuid.uuid4().hex[:6]}", rungs=MAINTENANCE,
        created_by=uuid.UUID(ADMIN_ID), jurisdiction_code="CA", rights_preserving=True,
    )
    yield lid
    _purge_ladder(su, lid)


def _task(su: psycopg.Connection, matter_id: str, task_type: str) -> uuid.UUID:
    row = su.execute(
        """
        insert into app.tasks (matter_id, title, deadline_type, task_type, final_due_date)
        values (%s, 'Maintenance fee', 'hard_external', %s, %s) returning id
        """,
        (matter_id, task_type, DEADLINE),
    ).fetchone()
    return uuid.UUID(str(row[0]))


def _task_type_of(su: psycopg.Connection, ladder_id: uuid.UUID) -> str:
    return str(su.execute(
        "select task_type from app.reminder_ladders where id = %s", (ladder_id,),
    ).fetchone()[0])


# --- ladder configuration ------------------------------------------------------


def test_a_bad_ladder_is_rejected_before_it_is_stored(su: psycopg.Connection) -> None:
    before = su.execute("select count(*) from app.reminder_ladders").fetchone()[0]
    with pytest.raises(rem.LadderConfigError):
        rem.save_ladder(su, "deadline", "bad", "x", [rem.Rung(1, 5, "l", "s", "b")],
                        created_by=uuid.UUID(ADMIN_ID))
    assert su.execute("select count(*) from app.reminder_ladders").fetchone()[0] == before


def test_a_jurisdiction_specific_ladder_beats_the_fallback(su: psycopg.Connection) -> None:
    task_type = f"oa_response_{uuid.uuid4().hex[:6]}"
    fallback = rem.save_ladder(su, "deadline", "any", task_type, MAINTENANCE,
                               created_by=uuid.UUID(ADMIN_ID))
    specific = rem.save_ladder(su, "deadline", "CA", task_type, MAINTENANCE,
                               created_by=uuid.UUID(ADMIN_ID), jurisdiction_code="CA")
    try:
        assert rem.match_ladder(su, "deadline", task_type, "CA") == specific
        assert rem.match_ladder(su, "deadline", task_type, "US") == fallback
        assert rem.match_ladder(su, "deadline", "nonexistent", "CA") is None
    finally:
        su.execute("delete from app.reminder_ladders where id in (%s, %s)", (fallback, specific))


# --- starting a schedule -------------------------------------------------------


def test_a_typed_task_with_a_matching_ladder_starts_a_schedule(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    assert sched is not None
    anchor = su.execute(
        "select anchor_date from app.reminder_schedules where id = %s", (sched,),
    ).fetchone()[0]
    assert anchor == DEADLINE, "a deadline ladder anchors on the task's final due date"


def test_an_untyped_task_is_simply_not_chased(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    row = su.execute(
        "insert into app.tasks (matter_id, title, deadline_type, final_due_date) "
        "values (%s, 'Untyped', 'internal', %s) returning id",
        (matter["matter"], DEADLINE),
    ).fetchone()
    assert rem.start_ladder_for_task(su, uuid.UUID(str(row[0]))) is None


def test_starting_twice_does_not_double_chase(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    assert rem.start_ladder_for_task(su, task_id) is not None
    assert rem.start_ladder_for_task(su, task_id) is None
    count = su.execute(
        "select count(*) from app.reminder_schedules where task_id = %s", (task_id,),
    ).fetchone()[0]
    assert count == 1


# --- the sweep -----------------------------------------------------------------


def test_the_sweep_never_sends_it_queues(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    """D31 (revised): every reminder passes the human audit queue in v1."""
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    result = rem.sweep_reminders(su, today=date(2026, 10, 2))  # T−60
    assert result.queued == 1

    row = su.execute(
        "select status::text, review_required, proposed_action_id, subject, sent_at "
        " from app.reminder_sends where schedule_id = %s", (sched,),
    ).fetchone()
    assert row[0] == "queued"
    assert row[1] is True
    assert row[2] is not None, "the rung must be sitting in the approval queue"
    assert matter["reference"] in row[3], "the subject renders with the matter's real reference"
    assert row[4] is None, "nothing is sent until a human approves"

    action = su.execute(
        "select agent_name, action_type, status::text from app.proposed_actions where id = %s",
        (row[2],),
    ).fetchone()
    assert action == ("a18-reminder", "reminder.send", "proposed")


def test_the_sweep_is_idempotent(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    rem.sweep_reminders(su, today=date(2026, 10, 2))
    second = rem.sweep_reminders(su, today=date(2026, 10, 2))
    assert second.queued == 0
    sends = su.execute(
        "select count(*) from app.reminder_sends where schedule_id = %s", (sched,),
    ).fetchone()[0]
    assert sends == 1


def test_a_missed_sweep_still_sends_the_final_reminder(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    """Catching up after an outage escalates rather than bursting three emails."""
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    result = rem.sweep_reminders(su, today=date(2026, 11, 20))
    assert (result.queued, result.superseded) == (1, 2)
    rows = su.execute(
        "select step_no, status::text, suppressed_reason from app.reminder_sends "
        " where schedule_id = %s order by step_no", (sched,),
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "suppressed"), (2, "suppressed"), (3, "queued")]
    assert rows[0][2] == "superseded by a later rung"


def test_an_unsubscribed_client_is_never_chased(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    su.execute(
        "insert into app.client_reminder_prefs (client_id, task_type, unsubscribed) "
        "values (%s, '', true)", (matter["client"],),
    )
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    result = rem.sweep_reminders(su, today=date(2026, 10, 2))
    assert (result.queued, result.suppressed) == (0, 1)
    row = su.execute(
        "select status::text, suppressed_reason, proposed_action_id from app.reminder_sends "
        " where schedule_id = %s", (sched,),
    ).fetchone()
    assert row[0] == "suppressed"
    assert row[1] == "client unsubscribed"
    assert row[2] is None, "a suppressed rung never reaches the approval queue"


def test_a_halted_ladder_stops_chasing(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    rem.start_ladder_for_task(su, task_id)
    rem.sweep_reminders(su, today=date(2026, 10, 2))
    halted = rem.halt_for_task(su, task_id, "client instructed us to pay (email 2026-10-05)")
    assert halted == 1
    assert rem.sweep_reminders(su, today=date(2026, 11, 2)).queued == 0
    row = su.execute(
        "select status::text, halted_reason from app.reminder_schedules where task_id = %s",
        (task_id,),
    ).fetchone()
    assert row[0] == "halted"
    assert "instructed" in row[1], "why we stopped chasing survives on the row"


def test_halting_requires_a_reason(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    assert sched is not None
    with pytest.raises(ValueError, match="reason"):
        rem.halt_schedule(su, sched, "   ")


def test_closing_the_task_stands_the_ladder_down(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    rem.start_ladder_for_task(su, task_id)
    su.execute("update app.tasks set status = 'completed' where id = %s", (task_id,))
    result = rem.sweep_reminders(su, today=date(2026, 10, 2))
    assert (result.cancelled, result.queued) == (1, 0)


# --- the safety rule: silence never abandons -----------------------------------


def test_an_exhausted_ladder_escalates_instead_of_closing(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    """D31's hard rule. Nobody replied to any rung; the ladder must hand a human an explicit
    pay-or-abandon decision rather than letting the fee lapse by default."""
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)

    status = su.execute(
        "select status::text from app.reminder_schedules where id = %s", (sched,),
    ).fetchone()[0]
    assert status == "escalated", "an exhausted ladder never sits in a terminal 'done' state"

    esc = su.execute(
        "select decision::text, decided_at from app.reminder_escalations where schedule_id = %s",
        (sched,),
    ).fetchone()
    assert esc == ("pending", None), "the decision is open until a human records it"


def test_escalation_is_created_once_not_per_sweep(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)
    su.execute("update app.reminder_schedules set status = 'active' where id = %s", (sched,))
    rem.sweep_reminders(su, today=date(2026, 11, 20))
    count = su.execute(
        "select count(*) from app.reminder_escalations where schedule_id = %s", (sched,),
    ).fetchone()[0]
    assert count == 1


def test_a_pending_escalation_shows_up_in_the_report(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)
    pending = [p for p in rem.pending_escalations(su) if str(p["schedule_id"]) == str(sched)]
    assert len(pending) == 1
    assert pending[0]["rights_preserving"] is True
    assert pending[0]["matter_reference"] == matter["reference"]


def test_recording_a_decision_clears_it_from_the_report(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)
    esc_id = su.execute(
        "select id from app.reminder_escalations where schedule_id = %s", (sched,),
    ).fetchone()[0]

    rem.record_decision(su, esc_id, "pay", uuid.UUID(ADMIN_ID), note="principal approved payment")
    row = su.execute(
        "select decision::text, decided_by, note from app.reminder_escalations where id = %s",
        (esc_id,),
    ).fetchone()
    assert row[0] == "pay"
    assert str(row[1]) == ADMIN_ID
    assert row[2] == "principal approved payment"
    assert not [p for p in rem.pending_escalations(su) if str(p["schedule_id"]) == str(sched)]


def test_pending_is_not_a_decision(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)
    esc_id = su.execute(
        "select id from app.reminder_escalations where schedule_id = %s", (sched,),
    ).fetchone()[0]
    with pytest.raises(ValueError, match="not a decision"):
        rem.record_decision(su, esc_id, "pending", uuid.UUID(ADMIN_ID))


def test_a_decision_cannot_be_overwritten(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    for day in (date(2026, 10, 2), date(2026, 11, 1), date(2026, 11, 17)):
        rem.sweep_reminders(su, today=day)
    esc_id = su.execute(
        "select id from app.reminder_escalations where schedule_id = %s", (sched,),
    ).fetchone()[0]
    rem.record_decision(su, esc_id, "abandon", uuid.UUID(ADMIN_ID))
    with pytest.raises(LookupError):
        rem.record_decision(su, esc_id, "pay", uuid.UUID(ADMIN_ID))


# --- approval executes the send ------------------------------------------------


def test_approving_the_proposal_marks_the_rung_sent(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    """The handler is registered on import of the domain module, so approval executes."""
    from py_shared.orchestrator import decide_action

    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    rem.sweep_reminders(su, today=date(2026, 10, 2))
    action_id = su.execute(
        "select proposed_action_id from app.reminder_sends where schedule_id = %s", (sched,),
    ).fetchone()[0]

    outcome = decide_action(su, action_id, approve=True, decided_by=ADMIN_ID)
    assert outcome.status == "executed"
    row = su.execute(
        "select status::text, sent_at, delivery_status from app.reminder_sends "
        " where schedule_id = %s", (sched,),
    ).fetchone()
    assert row[0] == "sent"
    assert row[1] is not None
    assert row[2] == "pending_transport", "transport lands with WP 4.3; we claim no delivery yet"


def test_rejecting_the_proposal_never_sends(
    su: psycopg.Connection, matter: dict[str, str], ladder: uuid.UUID,
) -> None:
    from py_shared.orchestrator import decide_action

    task_id = _task(su, matter["matter"], _task_type_of(su, ladder))
    sched = rem.start_ladder_for_task(su, task_id)
    rem.sweep_reminders(su, today=date(2026, 10, 2))
    action_id = su.execute(
        "select proposed_action_id from app.reminder_sends where schedule_id = %s", (sched,),
    ).fetchone()[0]

    decide_action(su, action_id, approve=False, decided_by=ADMIN_ID)
    row = su.execute(
        "select status::text, sent_at from app.reminder_sends where schedule_id = %s", (sched,),
    ).fetchone()
    assert row == ("queued", None)


# --- RLS (D43/D44), proved on a real user connection ---------------------------


def test_ordinary_staff_cannot_author_a_ladder(ladder: uuid.UUID) -> None:
    """What the firm says to clients under its own name is admin-gated, like docket rules."""
    with _user_conn(STAFF_ID) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        rem.save_ladder(conn, "deadline", "sneaky", "x",
                        [rem.Rung(1, -1, "l", "s", "b")], created_by=uuid.UUID(STAFF_ID))


def test_ordinary_staff_can_read_ladders(ladder: uuid.UUID) -> None:
    """Read access is the point — staff need to see what the client is being told and when."""
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select name from app.reminder_ladders where id = %s", (ladder,),
        ).fetchone()
    assert row is not None


def test_the_admin_can_author_a_ladder() -> None:
    with _user_conn(ADMIN_ID) as conn:
        lid = rem.save_ladder(conn, "deadline", "admin ladder",
                              f"tt_{uuid.uuid4().hex[:6]}", [rem.Rung(1, -1, "l", "s", "b")],
                              created_by=uuid.UUID(ADMIN_ID))
        assert lid is not None
        conn.rollback()


# --- awaiting-client follow-ups (spec §A18(b)) ---------------------------------


def test_a_follow_up_ladder_counts_forward_from_the_tag(
    su: psycopg.Connection, matter: dict[str, str],
) -> None:
    task_type = f"awaiting_docs_{uuid.uuid4().hex[:6]}"
    lid = rem.save_ladder(
        su, "awaiting_client", "every 7 days, capped at 3", task_type,
        [rem.Rung(1, 7, "1st", "{matter_reference}: still waiting", "b"),
         rem.Rung(2, 14, "2nd", "{matter_reference}: second request", "b"),
         rem.Rung(3, 21, "3rd", "{matter_reference}: third request", "b")],
        created_by=uuid.UUID(ADMIN_ID),
    )
    item = su.execute(
        "insert into app.work_items (title, matter_id, created_by) values ('Docs', %s, %s) "
        "returning id", (matter["matter"], ADMIN_ID),
    ).fetchone()[0]
    try:
        sched = rem.start_follow_up(su, uuid.UUID(str(item)), task_type, date(2026, 7, 1))
        assert sched is not None
        rem.sweep_reminders(su, today=date(2026, 7, 8))
        due = su.execute(
            "select due_on from app.reminder_sends where schedule_id = %s", (sched,),
        ).fetchone()[0]
        assert due == date(2026, 7, 8), "rung 1 falls 7 days AFTER the tag"

        # Run out the ladder: an unanswered follow-up escalates too, it does not just close.
        for day in (date(2026, 7, 15), date(2026, 7, 22)):
            rem.sweep_reminders(su, today=day)
        status = su.execute(
            "select status::text from app.reminder_schedules where id = %s", (sched,),
        ).fetchone()[0]
        assert status == "escalated"
    finally:
        _purge_ladder(su, lid)
