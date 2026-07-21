-- Migration 0027 — Report Builder core (WP 5B.1, spec §11B).
--
-- Reports are deliverables, not dashboards. What is durable is the *definition* — a dataset key
-- plus columns, filters, grouping and sort — never a SQL string: the builder in
-- py_shared.domain.reports resolves every identifier against its own dataset registry, so a
-- definition edited by hand still cannot express a query the registry did not sanction.
--
-- Sharing shares the definition, not the data. Runs execute on the viewer's own RLS-scoped
-- connection (D44), so a firm-shared report shows each person their own permitted rows and can
-- never surface a matter the recipient could not already open. That is why `shared` is a plain
-- boolean here and there is no row-level result cache — a cached result set would be exactly the
-- leak this design avoids.
--
-- Scheduling stores intent (frequency + hour), not a cron string: enough for "the Monday docket
-- report", checkable without a parser, and incapable of expressing a schedule the runner does not
-- understand. Delivery (PDF/spreadsheet by email, SFTP optional) lands with the Graph work in
-- WP 4.3; until then a run records what it produced.

create table app.reports (
  id                 uuid primary key default gen_random_uuid(),
  owner_id           uuid not null references app.os_users (id),
  name               text not null,
  dataset_key        text not null,        -- must exist in reports.DATASETS
  definition         jsonb not null,
  -- Firm-wide visibility. Deliberately coarse in v1: per-user shares would need their own ACL
  -- table, and RLS on the underlying data already stops a share over-sharing.
  shared             boolean not null default false,
  schedule_frequency text check (schedule_frequency in ('daily', 'weekly', 'monthly')),
  schedule_hour      int not null default 7 check (schedule_hour between 0 and 23),
  active             boolean not null default true,
  created_at         timestamptz not null default now(),
  unique (owner_id, name)
);

create index reports_shared_idx on app.reports (name) where shared and active;
create index reports_scheduled_idx on app.reports (schedule_frequency)
  where schedule_frequency is not null and active;

-- Every run, including failures: a schedule that silently produces nothing is indistinguishable
-- from one that produced an empty report, and the two need to be told apart.
create table app.report_runs (
  id           uuid primary key default gen_random_uuid(),
  report_id    uuid not null references app.reports (id) on delete cascade,
  requested_by uuid not null references app.os_users (id),
  run_at       timestamptz not null default now(),
  row_count    int not null default 0,
  status       text not null default 'ok' check (status in ('ok', 'failed')),
  error        text
);

create index report_runs_report_idx on app.report_runs (report_id, run_at desc);

-- ---------------------------------------------------------------------------
-- RLS (D43)
-- ---------------------------------------------------------------------------
--
-- A report is visible to its owner and, when shared, to any active staff member. Only the owner
-- may edit or delete it — a shared report is not a communal one, so nobody can quietly change
-- what a colleague's report means. Runs follow their report's visibility, and the insert policy
-- pins requested_by to the caller so a run log cannot be written in someone else's name.

create or replace function app.can_see_report(rid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.reports r
     where r.id = rid
       and (r.owner_id = app.jwt_uid() or (r.shared and app.is_active_staff()))
  );
$$;

revoke all on function app.can_see_report(uuid) from public;
grant execute on function app.can_see_report(uuid) to authenticated;

alter table app.reports     enable row level security;
alter table app.reports     force row level security;
alter table app.report_runs enable row level security;
alter table app.report_runs force row level security;

create policy reports_select on app.reports
  for select using (owner_id = app.jwt_uid() or (shared and app.is_active_staff()));
create policy reports_insert on app.reports
  for insert with check (owner_id = app.jwt_uid() and app.is_active_staff());
create policy reports_update on app.reports
  for update using (owner_id = app.jwt_uid()) with check (owner_id = app.jwt_uid());
create policy reports_delete on app.reports
  for delete using (owner_id = app.jwt_uid());

create policy report_runs_select on app.report_runs
  for select using (app.can_see_report(report_id));
create policy report_runs_insert on app.report_runs
  for insert with check (app.can_see_report(report_id) and requested_by = app.jwt_uid());

grant select, insert, update, delete on app.reports to authenticated;
grant select, insert on app.report_runs to authenticated;
