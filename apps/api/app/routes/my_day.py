"""My Day — each user's single unified queue (WP 5.5-lite, spec §12.0).

Track A ships this on the minimal work-item substrate: the union of the user's open docket tasks
(M1) and their open work items, priority-ordered by due proximity. Micro-requests + EOS To-Dos
join the union at their Phase-5 WPs — the shape here is designed to absorb them.

RLS-scoped (D44): the union runs on the caller's connection, so items on matters the caller cannot
see never appear. "The user" is the JWT subject — a user only ever sees their own My Day.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1", tags=["my-day"])


class MyDayItem(BaseModel):
    source: Literal["docket", "work_item"]
    id: UUID
    title: str
    matter_id: UUID | None
    matter_reference: str | None
    due_date: date | None          # earliest live date (respond_by → final_due_date → work due)
    deadline_type: str | None      # docket only
    status: str


_MY_DAY_SQL = """
select 'docket' as source, t.id, t.title, t.matter_id, m.reference,
       coalesce(t.respond_by, t.final_due_date) as due, t.deadline_type::text, t.status::text
  from app.tasks t
  join app.matters m on m.id = t.matter_id
 where t.assignee_id = %(me)s and t.status = 'open'
union all
select 'work_item', w.id, w.title, w.matter_id, m2.reference, w.due_date, null::text, w.status
  from app.work_items w
  left join app.matters m2 on m2.id = w.matter_id
 where w.assignee_id = %(me)s and w.status = 'open'
 order by due nulls last
 limit 500
"""


@router.get("/my-day", response_model=list[MyDayItem])
def my_day(identity: Identity) -> list[MyDayItem]:
    """The caller's unified, due-ordered queue of open docket tasks + work items (WP 5.5-lite)."""
    with identity.connection() as conn:
        rows = conn.execute(_MY_DAY_SQL, {"me": identity.entra.os_user_id}).fetchall()
    return [
        MyDayItem(
            source=r[0], id=r[1], title=r[2], matter_id=r[3], matter_reference=r[4],
            due_date=r[5], deadline_type=r[6], status=r[7],
        )
        for r in rows
    ]
