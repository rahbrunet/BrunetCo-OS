-- Migration 0014 — CIPO status watcher (WP 6.2, spec §A1).
--
-- Ports the legacy /opt/cipo-monitor state out of flat files into the DB:
--   * watcher_seen_docs — the presence-diff baseline (was seen_docs.json). A document is "new"
--     iff its identity key is absent from the matter's seen-set — NOT date-based, so same-day,
--     weekend and back-dated documents are all caught.
--   * watcher_failures — distinct failure tags per matter (was a spreadsheet Error column):
--     queryable, visible on the matter, feeds the ops dashboard.
--   * ops.watcher_runs — per-run summary log (was run_*.log + emailed report).
--   * matters.handled_by_others — per-matter suppression flag (was handled_by_others.txt):
--     maintenance fees paid by another firm; new docs recorded but no task generated.
--
-- Writes are system-worker-only (D44 enumerated exception); users get RLS-scoped reads.

-- Per-matter suppression flag (legacy #7 "handled by others").
alter table app.matters add column handled_by_others boolean not null default false;

-- ---------------------------------------------------------------------------
-- Presence-diff seen-state (was seen_docs.json)
-- ---------------------------------------------------------------------------

create table app.watcher_seen_docs (
  matter_id     uuid not null references app.matters (id) on delete cascade,
  doc_key       text not null,               -- normalized 'date|description' fingerprint
  first_seen_at timestamptz not null default now(),
  primary key (matter_id, doc_key)
);

-- ---------------------------------------------------------------------------
-- Failure tags (legacy Error-column discipline, now queryable)
-- ---------------------------------------------------------------------------

create type app.watcher_failure_tag as enum (
  'cipo_500',        -- CIPO's server cannot render the record (recheck later)
  'scrape_error',    -- other scrape exception after retries
  'no_data',         -- page loaded but the documents table stayed empty
  'download_failed'  -- document PDF download failed after retries
);

create table app.watcher_failures (
  id          uuid primary key default gen_random_uuid(),
  matter_id   uuid not null references app.matters (id) on delete cascade,
  run_id      uuid,                           -- ops.watcher_runs.id (soft ref; runs are ops-side)
  tag         app.watcher_failure_tag not null,
  detail      text,
  occurred_at timestamptz not null default now()
);

create index watcher_failures_matter_idx on app.watcher_failures (matter_id);
create index watcher_failures_run_idx on app.watcher_failures (run_id);

-- ---------------------------------------------------------------------------
-- Run log (ops — system-managed, dashboard-read)
-- ---------------------------------------------------------------------------

create table ops.watcher_runs (
  id          uuid primary key default gen_random_uuid(),
  agent_name  text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running' check (status in ('running', 'completed', 'crashed')),
  stats       jsonb not null default '{}'::jsonb   -- {rows, new, handled, downloaded, errors}
);

create index watcher_runs_agent_idx on ops.watcher_runs (agent_name, started_at desc);

-- ---------------------------------------------------------------------------
-- RLS — user reads follow matter visibility; writes are system-worker-only
-- ---------------------------------------------------------------------------

alter table app.watcher_seen_docs enable row level security;
alter table app.watcher_seen_docs force row level security;
alter table app.watcher_failures  enable row level security;
alter table app.watcher_failures  force row level security;

create policy watcher_seen_docs_select on app.watcher_seen_docs
  for select using (app.can_see_matter(matter_id));
create policy watcher_failures_select on app.watcher_failures
  for select using (app.can_see_matter(matter_id));

grant select on app.watcher_seen_docs to authenticated;
grant select on app.watcher_failures  to authenticated;
-- Run log is firm-general ops data (no matter linkage): staff-readable for the dashboard.
grant select on ops.watcher_runs to authenticated;

-- ---------------------------------------------------------------------------
-- Register the agent (A1) with the orchestrator (WP 6.1)
-- ---------------------------------------------------------------------------

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'cipo-watcher',
  'A1 — daily CIPO CPD status monitoring for Canadian patent matters',
  array['document.capture', 'task.create'],
  array['cipo/twocaptcha-api-key']
) on conflict (name) do nothing;
