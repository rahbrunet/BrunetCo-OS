-- Migration 0021 — micro-requests (WP 5.4, spec §M9, D30).
--
-- Tier 3 of the three-tier work model: intra-day @request items ("review this response before
-- filing"). The designed replacement for email ping-pong, so the design targets the specific
-- failures of email: a request that has no owner, no deadline, no thread, and no trace of how long
-- it took.
--
--   * SLA timer          — every request has a due time; turnaround is measured, not guessed.
--   * blocks its parent   — an open request on a work item BLOCKS that item, reusing the derived
--                           blocking from 5.1 so a task cannot be marked done while a review of it
--                           is still outstanding.
--   * unlimited round-trips — a request is a thread of messages, not a single hand-off, so the
--                           back-and-forth that would have been an email chain stays on the item.
--   * turnaround logging  — resolved_at − created_at, per request, feeding the productivity
--                           measurables (WP 5.8).

-- A request hangs off exactly one parent: a work item (blockable) or a document (annotatable).
create type app.micro_request_status as enum (
  'open',      -- awaiting the assignee
  'answered',  -- assignee replied; awaiting the requester (may re-open with another message)
  'resolved'   -- closed; stops blocking the parent
);

create table app.micro_requests (
  id                   uuid primary key default gen_random_uuid(),
  parent_work_item_id  uuid references app.work_items (id) on delete cascade,
  parent_document_id   uuid references app.documents (id) on delete cascade,
  requester_id         uuid not null references app.os_users (id),
  assignee_id          uuid not null references app.os_users (id),
  prompt               text not null,
  status               app.micro_request_status not null default 'open',
  sla_due              timestamptz,
  created_at           timestamptz not null default now(),
  resolved_at          timestamptz,
  resolved_by          uuid references app.os_users (id),
  -- Exactly one parent. A request that annotates nothing, or two things, is a bug in the caller.
  check (num_nonnulls(parent_work_item_id, parent_document_id) = 1)
);

create index micro_requests_parent_item_idx on app.micro_requests (parent_work_item_id);
create index micro_requests_assignee_open_idx
  on app.micro_requests (assignee_id) where status <> 'resolved';
create index micro_requests_requester_idx on app.micro_requests (requester_id);

-- The thread. Unlimited same-day round-trips = rows here, not a single answer column.
create table app.micro_request_messages (
  id         uuid primary key default gen_random_uuid(),
  request_id uuid not null references app.micro_requests (id) on delete cascade,
  author_id  uuid not null references app.os_users (id),
  body       text not null,
  created_at timestamptz not null default now()
);

create index micro_request_messages_request_idx
  on app.micro_request_messages (request_id, created_at);

-- ---------------------------------------------------------------------------
-- Parent blocking — extend the 5.1 derived-blocking function
-- ---------------------------------------------------------------------------
--
-- A work item is blocked if an incomplete PREDECESSOR exists (5.1) OR an unresolved micro-request
-- targets it (here). Centralising both sources in one function means My Day, boards and the
-- completion guard all see the same truth without each re-deriving it — and a task under review
-- cannot be quietly completed out from under the reviewer.

create or replace function app.work_item_is_blocked(item uuid) returns boolean
  language sql stable
as $$
  select
    exists (
      select 1
        from app.work_item_dependencies d
        join app.work_items w on w.id = d.depends_on_id
       where d.work_item_id = item
         and w.status not in ('done', 'cancelled')
    )
    or exists (
      select 1 from app.micro_requests r
       where r.parent_work_item_id = item and r.status <> 'resolved'
    );
$$;

-- ---------------------------------------------------------------------------
-- Turnaround
-- ---------------------------------------------------------------------------
--
-- Per-request turnaround and SLA outcome, for the person's own dashboard and the 5.8 measurables.
-- Computed from timestamps; nothing stored that could disagree with them.

create or replace view app.micro_request_turnaround as
  select
    r.id,
    r.assignee_id,
    r.requester_id,
    r.created_at,
    r.resolved_at,
    r.sla_due,
    case when r.resolved_at is not null
         then extract(epoch from (r.resolved_at - r.created_at)) / 3600.0
    end::numeric(10, 2) as turnaround_hours,
    case
      when r.resolved_at is null and r.sla_due is not null and r.sla_due < now() then 'breached'
      when r.resolved_at is not null and r.sla_due is not null and r.resolved_at > r.sla_due
        then 'late'
      when r.resolved_at is not null then 'on_time'
      else 'open'
    end as sla_outcome
  from app.micro_requests r;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- A request is visible to its requester, its assignee, and anyone who can see the parent it hangs
-- off (so a matter's team sees reviews on that matter's work). Messages inherit the request's
-- visibility. Both parties can act; the parent's team can read.

create or replace function app.can_see_micro_request(rid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.micro_requests r
     where r.id = rid
       and (
         r.requester_id = app.jwt_uid()
         or r.assignee_id = app.jwt_uid()
         or (r.parent_work_item_id is not null and exists (
              select 1 from app.work_items w
               where w.id = r.parent_work_item_id
                 and (w.matter_id is null or app.can_see_matter(w.matter_id))
                 and app.is_active_staff()
            ))
         or (r.parent_document_id is not null and app.can_see_document(r.parent_document_id))
       )
  );
$$;

revoke all on function app.can_see_micro_request(uuid) from public;
grant execute on function app.can_see_micro_request(uuid) to authenticated;

alter table app.micro_requests         enable row level security;
alter table app.micro_requests         force row level security;
alter table app.micro_request_messages enable row level security;
alter table app.micro_request_messages force row level security;

create policy micro_requests_select on app.micro_requests
  for select using (app.can_see_micro_request(id));
-- Anyone on staff who can see the parent may raise a request on it (@request is a floor action).
create policy micro_requests_insert on app.micro_requests
  for insert with check (
    app.is_active_staff() and requester_id = app.jwt_uid()
  );
-- Requester or assignee may advance it (answer, re-open, resolve).
create policy micro_requests_update on app.micro_requests
  for update using (
    requester_id = app.jwt_uid() or assignee_id = app.jwt_uid()
  );

create policy micro_request_messages_select on app.micro_request_messages
  for select using (app.can_see_micro_request(request_id));
create policy micro_request_messages_insert on app.micro_request_messages
  for insert with check (
    author_id = app.jwt_uid() and app.can_see_micro_request(request_id)
  );

grant select, insert, update on app.micro_requests to authenticated;
grant select, insert on app.micro_request_messages to authenticated;
grant select on app.micro_request_turnaround to authenticated;
