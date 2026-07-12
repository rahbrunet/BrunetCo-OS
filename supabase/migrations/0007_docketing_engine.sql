-- Migration 0007 — deadline engine substrate (WP 1.2): rules, holidays, provenance (M1-R14).
--
-- Rules are DATA in a declarative form (M1-R4): the `definition` jsonb is the single structured
-- representation the WP 1.3 dual-mode editor, the A2 NL round-trip, and the dry-run simulator
-- all read and write. Versioning is append-only: editing a rule inserts a new (rule_id, version)
-- row; tasks and provenance records pin the exact version that generated them.
--
-- Holiday calendars are per-jurisdiction (M1-R2). The engine rolls a computed date forward over
-- weekends and holidays and records each step in the provenance trace.
--
-- task_provenance is the M1-R14 log: permanent, immutable (no update/delete policies — even the
-- permissions admin cannot rewrite history), queryable by matter/rule/date range/trigger type.
-- The WP 1.7 parallel-run diff reads from this table.

-- ---------------------------------------------------------------------------
-- Docket rules (declarative, versioned, effective-dated)
-- ---------------------------------------------------------------------------

create table app.docket_rules (
  rule_id        uuid not null default gen_random_uuid(),
  version        int  not null default 1,
  name           text not null,
  -- Trigger this rule fires on: 'filing', 'allowance', 'task_completed:<code>', watcher codes …
  trigger_code   text not null,
  -- Restrict to a jurisdiction (internal disambiguated code); null = any jurisdiction.
  jurisdiction_code text,
  -- Declarative form (M1-R4). Engine-consumed keys (WP 1.2):
  --   title              task title template
  --   deadline_type      app.deadline_type value
  --   offsets            {respond_by: {years,months,days}, final_due_date: {years,months,days}}
  --   business_day_roll  bool — roll weekend/holiday dates forward (default true)
  --   completion_code    optional — completing the generated task fires 'task_completed:<code>'
  -- Later WPs add: owner resolution modes, alternate offset paths, matter-field setter actions
  -- (M1-R13) — stored here, ignored by the v1 engine until implemented.
  definition     jsonb not null,
  active         boolean not null default true,
  effective_from date not null default current_date,
  created_by     uuid not null references app.os_users (id),
  created_at     timestamptz not null default now(),
  primary key (rule_id, version)
);

create index docket_rules_trigger_idx on app.docket_rules (trigger_code) where active;

-- ---------------------------------------------------------------------------
-- Holiday calendars (M1-R2)
-- ---------------------------------------------------------------------------

create table app.holidays (
  jurisdiction_code text not null,          -- internal jurisdiction code ('CA', 'US', 'EP-EPO' …)
  holiday_date      date not null,
  name              text not null,
  primary key (jurisdiction_code, holiday_date)
);

-- ---------------------------------------------------------------------------
-- Task chaining: a task emits its rule's completion_code when completed (M1-R2 "rules chain").
-- ---------------------------------------------------------------------------

alter table app.tasks add column trigger_code text;

-- ---------------------------------------------------------------------------
-- M1-R14 provenance log
-- ---------------------------------------------------------------------------

create type app.trigger_type as enum (
  'event',            -- docket event fired on a matter (filing, allowance, watcher-observed …)
  'task_completion',  -- chained from completing another task
  'field_change',     -- matter-field change trigger (M1-R13, later WP)
  'watcher',          -- office-status watcher event (A1/A3)
  'manual'            -- staff-fired trigger
);

create table app.task_provenance (
  id               uuid primary key default gen_random_uuid(),
  task_id          uuid not null references app.tasks (id),
  matter_id        uuid not null references app.matters (id),
  family_id        uuid not null references app.families (id),
  rule_id          uuid not null,
  rule_version     int  not null,
  trigger_type     app.trigger_type not null,
  trigger_id       text,                    -- ID of the triggering item (task id, watcher event id …)
  input_dates      jsonb not null,          -- {"ref_date": "..."} plus any rule inputs
  -- Calculated dates including the full holiday-roll/extension trace, e.g.
  --   {"respond_by": {"raw": "...", "rolled": "...", "trace": [{"from":..,"to":..,"reason":..}]}, …}
  calculated_dates jsonb not null,
  generated_by     app.task_generator not null,
  generated_at     timestamptz not null default now(),
  foreign key (rule_id, rule_version) references app.docket_rules (rule_id, version)
);

create index task_provenance_matter_idx on app.task_provenance (matter_id);
create index task_provenance_rule_idx on app.task_provenance (rule_id);
create index task_provenance_generated_idx on app.task_provenance (generated_at);

-- ---------------------------------------------------------------------------
-- RLS (D43: every table ships its policy in the same migration)
-- ---------------------------------------------------------------------------

alter table app.docket_rules    enable row level security;
alter table app.docket_rules    force row level security;
alter table app.holidays        enable row level security;
alter table app.holidays        force row level security;
alter table app.task_provenance enable row level security;
alter table app.task_provenance force row level security;

-- Rules: firm-general read (the docket is everyone's business); writes gated to the permissions
-- admin until the WP 1.3 approval-gated editor lands (M1-R4 — no rule silently changes).
create policy docket_rules_select_staff on app.docket_rules
  for select using (app.is_active_staff());
create policy docket_rules_admin_write on app.docket_rules
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- Holidays: firm-general read; admin-maintained.
create policy holidays_select_staff on app.holidays
  for select using (app.is_active_staff());
create policy holidays_admin_write on app.holidays
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- Provenance: visibility follows the matter; inserts by staff who can see the matter.
-- Deliberately NO update or delete policy for anyone — the log is immutable (M1-R14/M1-R6).
create policy task_provenance_select on app.task_provenance
  for select using (app.can_see_matter(matter_id));
create policy task_provenance_insert on app.task_provenance
  for insert with check (app.can_see_matter(matter_id) and app.is_active_staff());

grant select, insert, update, delete on app.docket_rules, app.holidays to authenticated;
grant select, insert on app.task_provenance to authenticated;
