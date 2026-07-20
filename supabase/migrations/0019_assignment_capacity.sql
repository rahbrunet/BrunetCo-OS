-- Migration 0019 — assignment engine + capacity board (WP 5.2, spec §M9, D30).
--
-- Replaces the Word Patent/Trademark Project Lists: who does what, how loaded each person is, and
-- what has breached its SLA. Builds on the WP 5.1 work-item engine.
--
-- Four pieces:
--   * user_roles           — the capability POOL (who *can* perform a role), distinct from
--                            role_assignments (the single default). Workload-aware assignment
--                            needs a pool to choose from, not one pre-picked person.
--   * work_item_reassignments — an audit trail of moves. Drag-to-reassign is a real reassignment
--                            of someone's work, and "why is this mine now / no longer mine?" must
--                            be answerable.
--   * escalations          — SLA breaches routed to the responsible professional. An overdue task
--                            that only turns red on a board is a task nobody owns the escalation of.
--   * views                — workload, cycle-time history, and the capacity board itself, computed
--                            not stored (the same discipline as derived blocking in 5.1: a
--                            materialized load count is one more thing that drifts from reality).

-- ---------------------------------------------------------------------------
-- Capability pool
-- ---------------------------------------------------------------------------
--
-- role_assignments (0018) holds ONE default user per role. That is the fallback when nobody is a
-- better fit; assignment proper chooses among everyone who *can* do the role, which is this table.

create table app.user_roles (
  user_id uuid not null references app.os_users (id) on delete cascade,
  role    text not null,
  -- Optional cap on concurrent open items before the scorer treats the person as full. Null =
  -- no explicit cap (the scorer still prefers the least-loaded candidate).
  max_concurrent integer check (max_concurrent > 0),
  primary key (user_id, role)
);

create index user_roles_role_idx on app.user_roles (role);

-- ---------------------------------------------------------------------------
-- Reassignment audit
-- ---------------------------------------------------------------------------

create table app.work_item_reassignments (
  id            uuid primary key default gen_random_uuid(),
  work_item_id  uuid not null references app.work_items (id) on delete cascade,
  -- set null on user delete: a reassignment record must survive the departure of the person it
  -- moved work between — "reassigned from [former staff]" is still valid history.
  from_user_id  uuid references app.os_users (id) on delete set null,  -- null = was unassigned
  to_user_id    uuid references app.os_users (id) on delete set null,  -- null = unassigned (parked)
  reason        text,
  reassigned_by uuid not null references app.os_users (id),
  reassigned_at timestamptz not null default now()
);

create index work_item_reassignments_item_idx
  on app.work_item_reassignments (work_item_id, reassigned_at desc);

-- ---------------------------------------------------------------------------
-- SLA escalations
-- ---------------------------------------------------------------------------

create table app.escalations (
  id            uuid primary key default gen_random_uuid(),
  work_item_id  uuid not null references app.work_items (id) on delete cascade,
  escalated_to  uuid references app.os_users (id) on delete set null,  -- responsible professional
  reason        text not null,                       -- 'sla_breach', 'unassigned_overdue'
  due_date      date,
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz,
  -- One live escalation per item: re-running the sweep must not stack duplicates. Resolved rows
  -- are unconstrained so an item that breaches, is fixed, then breaches again keeps its history.
  resolved      boolean generated always as (resolved_at is not null) stored
);

create unique index escalations_one_open_per_item
  on app.escalations (work_item_id) where resolved_at is null;
create index escalations_target_idx on app.escalations (escalated_to) where resolved_at is null;

-- ---------------------------------------------------------------------------
-- Derived views — workload, cycle-time history, capacity board
-- ---------------------------------------------------------------------------

-- Live load per user: open + in-progress work items assigned to them.
create or replace view app.user_workload as
  select
    u.id as user_id,
    u.display_name,
    count(*) filter (where w.status in ('open', 'in_progress')) as open_load,
    count(*) filter (where w.status = 'in_progress')            as in_progress,
    count(*) filter (
      where w.status in ('open', 'in_progress') and w.due_date < current_date
    )                                                           as overdue,
    count(*) filter (
      where w.status in ('open', 'in_progress')
        and w.due_date between current_date and current_date + 7
    )                                                           as due_soon,
    min(w.due_date) filter (where w.status in ('open', 'in_progress')) as next_due
  from app.os_users u
  left join app.work_items w on w.assignee_id = u.id
  where u.is_active
  group by u.id, u.display_name;

-- Historical cycle time per user per task type, from completed items that carry both dates.
-- "Task type" is the template task_ref: the same logical step across projects (every "draft OA
-- response" is comparable), which is the grain the spec asks for ("per user per task type").
create or replace view app.user_task_cycle_stats as
  select
    w.assignee_id as user_id,
    w.task_ref,
    count(*)                                              as completed_count,
    avg((w.completed_on - w.started_on))::numeric(10, 2) as avg_cycle_days
  from app.work_items w
  where w.assignee_id is not null
    and w.task_ref is not null
    and w.status = 'done'
    and w.started_on is not null
    and w.completed_on is not null
  group by w.assignee_id, w.task_ref;

-- The board the owner reads: one row per active person with their live load. Drag-to-reassign is
-- an update to work_items.assignee_id (logged via work_item_reassignments); the board is the read.
create or replace view app.capacity_board as
  select
    wl.user_id,
    wl.display_name,
    wl.open_load,
    wl.in_progress,
    wl.overdue,
    wl.due_soon,
    wl.next_due,
    -- Roles the person can take, so the board can show who is eligible for reassignment.
    coalesce(array_agg(distinct ur.role) filter (where ur.role is not null), '{}') as roles
  from app.user_workload wl
  left join app.user_roles ur on ur.user_id = wl.user_id
  group by wl.user_id, wl.display_name, wl.open_load, wl.in_progress,
           wl.overdue, wl.due_soon, wl.next_due;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- Capability + capacity are firm-general operational data (assigning work is everyone's concern),
-- readable by staff. Editing the capability pool is admin-gated — it changes who the assignment
-- engine will hand work to. Escalations follow the visibility of the item they concern.

alter table app.user_roles              enable row level security;
alter table app.user_roles              force row level security;
alter table app.work_item_reassignments enable row level security;
alter table app.work_item_reassignments force row level security;
alter table app.escalations             enable row level security;
alter table app.escalations             force row level security;

create policy user_roles_read on app.user_roles
  for select using (app.is_active_staff());
create policy user_roles_write on app.user_roles
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- Reassignment history is visible to whoever can see the work item; any staff member may record a
-- reassignment of an item they can see (drag-to-reassign is a floor-level action, not admin-only).
create policy work_item_reassignments_select on app.work_item_reassignments
  for select using (
    exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
    )
  );
create policy work_item_reassignments_insert on app.work_item_reassignments
  for insert with check (
    app.is_active_staff()
    and exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
    )
  );

create policy escalations_select on app.escalations
  for select using (
    exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
    )
  );
-- The sweep runs as a system worker (D44 enumerated exception); staff may resolve an escalation
-- on an item they can see.
create policy escalations_update on app.escalations
  for update using (
    app.is_active_staff()
    and exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
    )
  );

grant select, insert, update, delete on app.user_roles to authenticated;
grant select, insert on app.work_item_reassignments to authenticated;
grant select, update on app.escalations to authenticated;
grant select on app.user_workload, app.user_task_cycle_stats, app.capacity_board to authenticated;
