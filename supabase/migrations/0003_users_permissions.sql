-- Migration 0003 — OS users + D43 permission framework (WP 0.8).
--
-- D43: role-based permissions layered over family-level ACLs. Firm-general access is what any
-- active staff member gets; five separately grantable protected domains guard financial data:
--   time_entry / expense_entry / invoicing / accounting_reporting / compensation_admin
-- Role templates (Agent / Paralegal / Bookkeeper / Principal) are named bundles of default
-- grants — applying a template inserts grants; grants remain individually revocable.
-- Own-record rule: a user always sees their OWN records (time entries, bonus statements) even
-- without the domain grant; the domain grant is what opens OTHER people's records.
--
-- Every table ships its RLS policy in this same migration (D43 — no "temporarily open" tables).
-- Policies use app.jwt_uid() from migration 0001 (D44 user-JWT pass-through).

-- ---------------------------------------------------------------------------
-- Users
-- ---------------------------------------------------------------------------

create table app.os_users (
  id            uuid primary key,                 -- = JWT sub (minted by the D44 bridge)
  email         text not null unique,
  display_name  text not null,
  entra_oid     text unique,                      -- Entra object id, bound at first login (WP 0.8+)
  role_template text,                             -- last-applied template (informational; grants govern)
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

alter table app.os_users enable row level security;
alter table app.os_users force row level security;

-- ---------------------------------------------------------------------------
-- Permission domains + grants
-- ---------------------------------------------------------------------------

create type app.permission_domain as enum (
  'time_entry',            -- enter/see others' time
  'expense_entry',         -- enter/see others' expenses
  'invoicing',             -- WIP review, draft/push invoices
  'accounting_reporting',  -- firm-level financial reporting (separate from entry, D43)
  'compensation_admin'     -- bonus rate matrix + others' bonus statements
);

create table app.permission_grants (
  user_id    uuid not null references app.os_users (id) on delete cascade,
  domain     app.permission_domain not null,
  granted_by uuid not null references app.os_users (id),
  granted_at timestamptz not null default now(),
  primary key (user_id, domain)
);

alter table app.permission_grants enable row level security;
alter table app.permission_grants force row level security;

-- Role templates: named default-grant bundles. Data, not code, so the permissions-admin
-- screen can render and edit them. Seeded below per D43.
create table app.role_templates (
  name    text primary key,
  domains app.permission_domain[] not null
);

alter table app.role_templates enable row level security;
alter table app.role_templates force row level security;

insert into app.role_templates (name, domains) values
  ('Agent',      '{time_entry,expense_entry}'),
  ('Paralegal',  '{time_entry,expense_entry}'),
  ('Bookkeeper', '{time_entry,expense_entry,invoicing,accounting_reporting}'),
  ('Principal',  '{time_entry,expense_entry,invoicing,accounting_reporting,compensation_admin}');

-- ---------------------------------------------------------------------------
-- Helper functions (security definer: policies must be able to consult these tables
-- without recursing into their own RLS)
-- ---------------------------------------------------------------------------

create or replace function app.is_active_staff() returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.os_users u
     where u.id = app.jwt_uid() and u.is_active
  );
$$;

create or replace function app.has_domain(d app.permission_domain) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.permission_grants g
     where g.user_id = app.jwt_uid() and g.domain = d
  );
$$;

-- Permissions administration itself is gated by compensation_admin's holder — in practice the
-- Principal template. Kept as its own predicate so the gate can change in one place.
create or replace function app.is_permissions_admin() returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select app.has_domain('compensation_admin');
$$;

revoke all on function app.is_active_staff() from public;
revoke all on function app.has_domain(app.permission_domain) from public;
revoke all on function app.is_permissions_admin() from public;
grant execute on function app.is_active_staff() to authenticated;
grant execute on function app.has_domain(app.permission_domain) to authenticated;
grant execute on function app.is_permissions_admin() to authenticated;

-- ---------------------------------------------------------------------------
-- Policies
-- ---------------------------------------------------------------------------

-- os_users: any active staff can see the roster (names/emails are firm-general);
-- only the permissions admin may write.
create policy os_users_select_staff on app.os_users
  for select using (app.is_active_staff() or id = app.jwt_uid());

create policy os_users_admin_write on app.os_users
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- permission_grants: own grants visible (so the UI can show "what can I do");
-- admins see and manage all.
create policy grants_select_own_or_admin on app.permission_grants
  for select using (user_id = app.jwt_uid() or app.is_permissions_admin());

create policy grants_admin_write on app.permission_grants
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- role_templates: readable by staff; writable by the permissions admin.
create policy role_templates_select_staff on app.role_templates
  for select using (app.is_active_staff());

create policy role_templates_admin_write on app.role_templates
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

grant select on app.os_users, app.permission_grants, app.role_templates to authenticated;
grant insert, update, delete on app.os_users, app.permission_grants, app.role_templates to authenticated;
