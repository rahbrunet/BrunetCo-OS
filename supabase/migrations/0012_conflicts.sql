-- Migration 0012 — conflicts database + checks (WP 5B.4, D34/D38).
--
-- "No matter opens without a conflicts check" (D38 pulled 5B.4 into Track A). Two entry points:
-- intake (before opening a matter) and on-demand. Every run is LOGGED with its results so the
-- firm can show a conflict was actually cleared.
--
-- A conflict check must see the WHOLE firm to be meaningful — a restricted family the searcher
-- cannot normally see is exactly the conflict that matters. So the search runs in a SECURITY
-- DEFINER function that bypasses per-user RLS by design; it returns match SUMMARIES (label +
-- what matched), and the run is recorded in app.conflict_checks. This RLS bypass is deliberate
-- and narrow (one function, summaries only) — noted for the D44 service-role/definer inventory.

create extension if not exists pg_trgm;

-- ---------------------------------------------------------------------------
-- Conflict-check log
-- ---------------------------------------------------------------------------

create type app.conflict_check_type as enum ('intake', 'on_demand');

create table app.conflict_checks (
  id           uuid primary key default gen_random_uuid(),
  query_text   text not null,
  check_type   app.conflict_check_type not null default 'on_demand',
  matter_id    uuid references app.matters (id),   -- set for intake checks tied to an opening
  result_count int not null,
  results      jsonb not null,                      -- [{kind, ref, label, matched_on, score}, …]
  cleared      boolean not null default false,      -- a human marked "no true conflict"
  notes        text,
  run_by       uuid not null references app.os_users (id),
  run_at       timestamptz not null default now(),
  cleared_by   uuid references app.os_users (id),
  cleared_at   timestamptz
);

create index conflict_checks_matter_idx on app.conflict_checks (matter_id);
create index conflict_checks_run_idx on app.conflict_checks (run_at);

-- ---------------------------------------------------------------------------
-- Firm-wide conflict search (SECURITY DEFINER — sees all families/matters/parties)
-- ---------------------------------------------------------------------------

create or replace function app.search_conflicts(q text, min_score real default 0.3)
  returns table (kind text, ref text, label text, matched_on text, score real)
  language sql stable security definer set search_path = app, public
as $$
  with hits as (
    select 'client'::text as kind, c.code as ref, c.name as label,
           'client_name'::text as matched_on, similarity(c.name, q) as score
      from app.clients c
     where c.name % q or c.name ilike '%' || q || '%'
    union all
    select 'contact', ct.id::text, ct.full_name, 'contact', similarity(ct.full_name, q)
      from app.contacts ct
     where ct.full_name % q or ct.full_name ilike '%' || q || '%'
    union all
    select 'family', f.reference, f.title, 'family_title', similarity(f.title, q)
      from app.families f
     where f.title % q or f.title ilike '%' || q || '%'
    union all
    select 'matter', m.reference, m.reference, 'matter_reference', similarity(m.reference, q)
      from app.matters m
     where m.reference % q or m.reference ilike '%' || q || '%'
        or (m.application_no is not null and m.application_no ilike '%' || q || '%')
  )
  select kind, ref, label, matched_on,
         greatest(score, case when label ilike '%' || q || '%' then 0.5 else 0 end)::real as score
    from hits
   where greatest(score, case when label ilike '%' || q || '%' then 0.5 else 0 end) >= min_score
   order by score desc
   limit 50;
$$;

revoke all on function app.search_conflicts(text, real) from public;
grant execute on function app.search_conflicts(text, real) to authenticated;

-- ---------------------------------------------------------------------------
-- RLS — the check LOG is firm-general (a conflict check is a firm-integrity record); the search
-- itself is the definer function above.
-- ---------------------------------------------------------------------------

alter table app.conflict_checks enable row level security;
alter table app.conflict_checks force row level security;

create policy conflict_checks_select on app.conflict_checks
  for select using (app.is_active_staff());
create policy conflict_checks_insert on app.conflict_checks
  for insert with check (app.is_active_staff());
create policy conflict_checks_update on app.conflict_checks
  for update using (app.is_active_staff()) with check (app.is_active_staff());

grant select, insert, update on app.conflict_checks to authenticated;
