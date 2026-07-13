"""Annuity / maintenance-fee series generation (WP 1.8, M1-R8).

Patent maintenance deadlines are generated in-house per jurisdiction as a SERIES spanning the
life of the patent, counted from the matter's base date (filing for CIPO/EPO, grant for USPTO).
Each entry becomes a docket task + M1-R14 provenance record, feeding the §7.1 pay/abandon
instruction workflow and the A18 reminder ladders.

Date math reuses the WP 1.2 deadline primitives (add_offset, roll_forward) so annuity dates roll
over the same jurisdiction holiday calendars and record the same trace. Generation is idempotent:
the tasks.annuity_seq unique index means re-running never double-dockets a year.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg

from py_shared.domain.deadlines import CalculatedDate, add_offset, roll_forward
from py_shared.domain.docketing import load_holidays


@dataclass
class AnnuityTask:
    task_id: UUID
    provenance_id: UUID
    annuity_seq: int
    year_label: str        # '5' or '3.5'
    respond_by: date       # the fee due date (anniversary)
    final_due_date: date   # end of the grace / surcharge window


class AnnuityError(RuntimeError):
    """The series cannot be generated (no schedule, or the base date is missing)."""


def _schedule_years(
    first_year: Decimal | None,
    last_year: Decimal | None,
    year_interval: Decimal,
    explicit_years: list[Decimal] | None,
) -> list[Decimal]:
    """Ordered list of anniversary years: explicit list wins, else first..last step interval."""
    if explicit_years:
        return sorted(explicit_years)
    assert first_year is not None and last_year is not None
    years: list[Decimal] = []
    y = first_year
    while y <= last_year:
        years.append(y)
        y += year_interval
    return years


def _anniversary(base: date, years: Decimal, due_rule: str) -> date:
    """Anniversary date `years` after `base`. Fractional years add whole months (0.5 → 6 months).
    'month_end_anniversary' snaps to the last day of the resulting month (EPO convention)."""
    whole = int(years)
    extra_months = int(round((years - whole) * 12))
    d = add_offset(base, years=whole, months=extra_months)
    if due_rule == "month_end_anniversary":
        last = calendar.monthrange(d.year, d.month)[1]
        d = date(d.year, d.month, last)
    return d


def _year_label(years: Decimal) -> str:
    """'5' for whole years, '3.5' for fractional."""
    return str(int(years)) if years == years.to_integral_value() else str(years.normalize())


def generate_annuity_tasks(
    conn: psycopg.Connection,
    matter_id: UUID,
    generated_by: str = "rule_engine",
) -> list[AnnuityTask]:
    """Generate the full maintenance-fee series for a matter (idempotent).

    Loads the matter's jurisdiction schedule and base date, computes each anniversary (rolled over
    the jurisdiction holiday calendar), and inserts any not-yet-docketed entries as tasks +
    provenance. Returns only the NEWLY created tasks (existing ones are skipped via the annuity_seq
    unique index). Raises AnnuityError if there is no schedule or the base date is missing.
    """
    matter = conn.execute(
        """
        select m.family_id, m.jurisdiction_code, m.filing_date, m.registration_date
          from app.matters m where m.id = %s
        """,
        (matter_id,),
    ).fetchone()
    if matter is None:
        raise LookupError("matter not found or not visible")
    family_id, jurisdiction, filing_date, registration_date = matter

    sched = conn.execute(
        """
        select base_event, first_year, last_year, year_interval, explicit_years, due_rule,
               grace_months, deadline_type
          from app.annuity_schedules where jurisdiction_code = %s and active
        """,
        (jurisdiction,),
    ).fetchone()
    if sched is None:
        raise AnnuityError(f"no active annuity schedule for jurisdiction {jurisdiction!r}")
    (base_event, first_year, last_year, year_interval, explicit_years, due_rule,
     grace_months, deadline_type) = sched

    base = filing_date if base_event == "filing" else registration_date
    if base is None:
        raise AnnuityError(
            f"matter has no {base_event} date — cannot generate the {jurisdiction} annuity series"
        )

    holidays = load_holidays(conn, jurisdiction)
    years = _schedule_years(first_year, last_year, year_interval, explicit_years)

    created: list[AnnuityTask] = []
    for seq, years_val in enumerate(years, start=1):
        anniversary = _anniversary(base, years_val, due_rule)
        respond_rolled, respond_trace = roll_forward(anniversary, holidays)
        grace_raw = add_offset(respond_rolled, months=int(grace_months))
        final_rolled, final_trace = roll_forward(grace_raw, holidays)
        label = _year_label(years_val)
        title = f"Maintenance fee — year {label} ({jurisdiction})"

        task_id = uuid4()
        inserted = conn.execute(
            """
            insert into app.tasks
              (id, matter_id, title, deadline_type, ref_date, respond_by, final_due_date,
               generated_by, annuity_seq)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (matter_id, annuity_seq) where annuity_seq is not null do nothing
            returning id
            """,
            (task_id, matter_id, title, deadline_type, base, respond_rolled, final_rolled,
             generated_by, seq),
        ).fetchone()
        if inserted is None:
            continue  # already docketed this year — idempotent skip

        calculated = {
            "respond_by": CalculatedDate(anniversary, respond_rolled, respond_trace).as_json(),
            "final_due_date": CalculatedDate(grace_raw, final_rolled, final_trace).as_json(),
        }
        provenance_id = uuid4()
        conn.execute(
            """
            insert into app.task_provenance
              (id, task_id, matter_id, family_id, trigger_type, trigger_id, input_dates,
               calculated_dates, generated_by, source_ref)
            values (%s, %s, %s, %s, 'event', %s, %s, %s, %s, %s)
            """,
            (provenance_id, task_id, matter_id, family_id, f"annuity:{jurisdiction}:{label}",
             json.dumps({"base_event": base_event, "base_date": base.isoformat()}),
             json.dumps(calculated), generated_by, f"annuity:{jurisdiction}:{label}"),
        )
        created.append(AnnuityTask(
            task_id=task_id, provenance_id=provenance_id, annuity_seq=seq, year_label=label,
            respond_by=respond_rolled, final_due_date=final_rolled,
        ))
    return created
