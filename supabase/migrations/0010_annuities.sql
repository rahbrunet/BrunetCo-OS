-- Migration 0010 — annuity / maintenance-fee docketing (WP 1.8, M1-R8).
--
-- Maintenance (patent annuity / renewal) deadlines are generated IN-HOUSE per jurisdiction from
-- the matter's base date — not from a single-trigger docket_rule, but as a SERIES spanning the
-- life of the patent. CIPO: annual from the 2nd anniversary of filing; EPO: annual from the 3rd
-- year, due the last day of the filing-anniversary month; USPTO: at 3.5 / 7.5 / 11.5 years from
-- grant. The series feeds the §7.1 annuity instruction workflow (pay / abandon) and the A18
-- reminder ladders.
--
-- Two schema touches:
--   1. tasks.annuity_seq — the ordinal of a maintenance task within its matter's series, giving a
--      natural idempotency key so re-running the generator never double-dockets a year.
--   2. task_provenance — annuity tasks are rule-less (no docket_rules row), so rule_id/rule_version
--      become nullable and a source_ref text records what generated them ('annuity:CA:5'). M1-R14
--      still holds: every generated task carries a queryable provenance record with its date trace.

-- ---------------------------------------------------------------------------
-- Annuity schedules (data — one header row per jurisdiction)
-- ---------------------------------------------------------------------------

create table app.annuity_schedules (
  jurisdiction_code text primary key,
  -- Base date the series counts from.
  base_event        text not null check (base_event in ('filing', 'grant')),
  -- Range form (CA 2..20, EPO 3..20) OR explicit_years (USPTO {3.5, 7.5, 11.5}). If
  -- explicit_years is set it wins; otherwise first_year..last_year step year_interval.
  first_year        numeric,
  last_year         numeric,
  year_interval     numeric not null default 1,
  explicit_years    numeric[],
  -- How the anniversary date is placed.
  due_rule          text not null default 'anniversary'
                    check (due_rule in ('anniversary', 'month_end_anniversary')),
  grace_months      int not null default 6,          -- late-payment / surcharge window
  deadline_type     app.deadline_type not null default 'extendable_external',
  active            boolean not null default true,
  check (explicit_years is not null or (first_year is not null and last_year is not null))
);

alter table app.tasks add column annuity_seq int;

-- One maintenance task per (matter, ordinal) — the generator relies on this for idempotency.
create unique index tasks_annuity_uq on app.tasks (matter_id, annuity_seq)
  where annuity_seq is not null;

-- ---------------------------------------------------------------------------
-- Provenance: allow rule-less (annuity) generation.
-- ---------------------------------------------------------------------------

alter table app.task_provenance alter column rule_id drop not null;
alter table app.task_provenance alter column rule_version drop not null;
alter table app.task_provenance add column source_ref text;   -- e.g. 'annuity:CA:5'

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.annuity_schedules enable row level security;
alter table app.annuity_schedules force row level security;

-- Schedules are firm-general read (like holidays), admin-maintained.
create policy annuity_schedules_select on app.annuity_schedules
  for select using (app.is_active_staff());
create policy annuity_schedules_admin_write on app.annuity_schedules
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

grant select, insert, update, delete on app.annuity_schedules to authenticated;

-- ---------------------------------------------------------------------------
-- Seed the v1 jurisdictions (CIPO/USPTO/EPO — WIPO/PCT has no international-phase annuities).
-- Amounts are NOT here (M4 fee schedule owns money); this is deadline dates only.
-- ---------------------------------------------------------------------------

insert into app.annuity_schedules
  (jurisdiction_code, base_event, first_year, last_year, year_interval, explicit_years,
   due_rule, grace_months, deadline_type)
values
  -- CIPO: annual maintenance fees from the 2nd anniversary of filing (Patent Act), 6-month grace.
  ('CA', 'filing', 2, 20, 1, null, 'anniversary', 6, 'extendable_external'),
  -- EPO: renewal fees from the 3rd year, due the last day of the filing-anniversary month.
  ('EP-EPO', 'filing', 3, 20, 1, null, 'month_end_anniversary', 6, 'extendable_external'),
  -- USPTO: maintenance fees at 3.5 / 7.5 / 11.5 years from grant, 6-month grace with surcharge.
  ('US', 'grant', null, null, 1, array[3.5, 7.5, 11.5]::numeric[], 'anniversary', 6,
   'extendable_external')
on conflict (jurisdiction_code) do nothing;
