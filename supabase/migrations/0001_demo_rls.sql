-- Migration 0001 — demo RLS table (WP 0.7 proof of the D44 access-control pattern).
--
-- D43 ruling: every table ships its RLS policy in the same migration — no "temporarily open"
-- tables. This demo table is the pattern WP 0.8 domain tables and the WP 9.1 permission
-- acceptance tests reuse.
--
-- Portability note: policies key off `app.jwt_uid()`, which reads the `sub` claim from
-- `request.jwt.claims`. This is equivalent to Supabase's `auth.uid()` but does not depend on
-- the `auth` schema, so the same migration + RLS proof runs on a plain Postgres CI service and
-- on the Supabase local stack alike.

create schema if not exists app;

-- The `authenticated` role exists in Supabase; create it for plain-Postgres CI if missing.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
end
$$;

-- Resolve the calling user's UUID from the request JWT claims set by py_shared.auth.
create or replace function app.jwt_uid() returns uuid
  language sql stable
as $$
  select nullif(
    current_setting('request.jwt.claims', true)::json ->> 'sub', ''
  )::uuid;
$$;

create table app.demo_notes (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null default app.jwt_uid(),
  body       text not null,
  created_at timestamptz not null default now()
);

alter table app.demo_notes enable row level security;
alter table app.demo_notes force row level security;  -- owner is subject to RLS too

-- A user may only see and create their own rows.
create policy demo_notes_select_own on app.demo_notes
  for select using (owner_id = app.jwt_uid());

create policy demo_notes_insert_own on app.demo_notes
  for insert with check (owner_id = app.jwt_uid());

grant usage on schema app to authenticated;
grant select, insert on app.demo_notes to authenticated;
