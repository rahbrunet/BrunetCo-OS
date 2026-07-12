-- Migration 0005 — docket tasks (M1-R11/R12) + minimal work-item substrate (WP 0.8).
--
-- Tasks are docket items: every one carries dual dates (RespondBy + FinalDueDate), a trigger
-- RefDate, a ClosedOn date, and a six-value DeadlineType (confirmed by the D35/D37 CSV analysis).
-- DeadlineType drives A18's reminder ladders and scopes the "silence never abandons" rule
-- (hard_external + extendable_external only).
--
-- The work-item substrate is deliberately minimal (record + CRUD, no board features): WP 2.5.1
-- time entries must link to work items before the full Phase 5 engine exists (tracker v17 risk
-- note), and WP 5.5 My Day reads the union of docket tasks + work items.
--
-- M1-R14 provenance: the full provenance log table ships with the deadline engine (WP 1.2) —
-- tasks reserve the generator/rule columns now so no backfill is needed.

create type app.deadline_type as enum (
  'hard_external',        -- real legal consequence; never abandoned on silence
  'extendable_external',  -- extension available (with its own fee logic)
  'internal',
  'general_reminder',
  'event',
  'transient_event'
);

create type app.task_status as enum (
  'open',
  'completed',
  'received',    -- D35 taxonomy
  'not_needed',
  'missed'       -- deadline passed; extension now required
);

create type app.awaiting_state as enum (
  'awaiting_client',   -- A18 ladder territory
  'awaiting_office',   -- M1-R12: "expect X by [date]"; overdue = escalate/query office
  'blocked_on_review'  -- micro-request territory (Phase 5)
);

create type app.task_generator as enum ('rule_engine', 'agent', 'manual');

create table app.tasks (
  id             uuid primary key default gen_random_uuid(),
  matter_id      uuid not null references app.matters (id),
  title          text not null,
  deadline_type  app.deadline_type not null,
  status         app.task_status not null default 'open',
  awaiting       app.awaiting_state,
  ref_date       date,                     -- trigger date the rule fired from
  respond_by     date,
  final_due_date date,
  closed_on      date,
  assignee_id    uuid references app.os_users (id),
  -- M1-R14 provenance reservation (full log table lands with WP 1.2):
  generated_by   app.task_generator not null default 'manual',
  rule_id        uuid,
  rule_version   int,
  created_at     timestamptz not null default now()
);

create index tasks_matter_idx on app.tasks (matter_id);
create index tasks_due_idx on app.tasks (final_due_date) where status = 'open';
create index tasks_assignee_idx on app.tasks (assignee_id) where status = 'open';

-- Minimal work-item substrate (WP 5.5-lite / 2.5.1 dependency).
create table app.work_items (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  matter_id   uuid references app.matters (id),   -- optional: general firm work exists
  assignee_id uuid references app.os_users (id),
  status      text not null default 'open' check (status in ('open', 'done')),
  due_date    date,
  created_by  uuid not null references app.os_users (id),
  created_at  timestamptz not null default now()
);

create index work_items_assignee_idx on app.work_items (assignee_id) where status = 'open';

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.tasks      enable row level security;
alter table app.tasks      force row level security;
alter table app.work_items enable row level security;
alter table app.work_items force row level security;

create or replace function app.can_see_matter(mid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.matters m where m.id = mid and app.can_see_family(m.family_id)
  );
$$;

revoke all on function app.can_see_matter(uuid) from public;
grant execute on function app.can_see_matter(uuid) to authenticated;

-- Tasks follow matter/family visibility.
create policy tasks_select on app.tasks
  for select using (app.can_see_matter(matter_id));
create policy tasks_write on app.tasks
  for insert with check (app.can_see_matter(matter_id) and app.is_active_staff());
create policy tasks_update on app.tasks
  for update using (app.can_see_matter(matter_id) and app.is_active_staff())
  with check (app.can_see_matter(matter_id));

-- Work items: firm-general, but matter-linked items follow matter visibility.
create policy work_items_select on app.work_items
  for select using (
    app.is_active_staff()
    and (matter_id is null or app.can_see_matter(matter_id))
  );
create policy work_items_write on app.work_items
  for insert with check (
    app.is_active_staff()
    and (matter_id is null or app.can_see_matter(matter_id))
  );
create policy work_items_update on app.work_items
  for update using (
    app.is_active_staff()
    and (matter_id is null or app.can_see_matter(matter_id))
  )
  with check (matter_id is null or app.can_see_matter(matter_id));

grant select, insert, update, delete on app.tasks, app.work_items to authenticated;
