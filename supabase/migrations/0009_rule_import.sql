-- Migration 0009 — rule import + management (WP 1.3).
--
-- WP 1.2 shipped the engine + the declarative `docket_rules.definition`. This WP adds the
-- machinery to (a) import the 552 legacy AppColl task-type rules into that form, (b) manage them
-- through an approval-gated, versioned editor, and (c) hold the rows that could not be mapped
-- cleanly (opaque trigger linkage, etc.) for manual resolution rather than dropping them.
--
-- No engine change: imported rules land inactive + approval_status='draft', so the WP 1.2
-- selector (which fires only `active` rules) never fires an unapproved rule. Approval flips both
-- flags in one step. Existing seed/migration rules are back-filled to 'approved' so they keep
-- firing.

-- ---------------------------------------------------------------------------
-- Rule provenance + approval state on docket_rules
-- ---------------------------------------------------------------------------

create type app.rule_approval_status as enum ('draft', 'approved', 'rejected');
create type app.rule_source as enum ('migration', 'appcoll_import', 'manual', 'ui_edit');

alter table app.docket_rules
  add column approval_status     app.rule_approval_status not null default 'approved',
  add column source              app.rule_source          not null default 'migration',
  -- AppColl TaskType identity — the idempotency key for re-runnable imports. Null for
  -- OS-authored rules. Unique per (id) so a re-import updates rather than duplicates.
  add column appcoll_task_type_id text,
  -- Migration tags, e.g. 'superseded-by-a1' (USPTO-integration rules the watcher framework
  -- replaces — imported but inactive), 'migrate-to-a18-ladder' (reminder-half task types).
  add column import_tags         text[] not null default '{}';

-- One imported AppColl task type maps to one rule lineage (all its versions share the id).
create unique index docket_rules_appcoll_idx
  on app.docket_rules (appcoll_task_type_id)
  where appcoll_task_type_id is not null and version = 1;

-- ---------------------------------------------------------------------------
-- Manual-resolution queue: AppColl rows that did not map cleanly (D37: trigger-event linkage
-- is via opaque IDs absent from the export, so some rows cannot be auto-resolved). Nothing is
-- ever silently dropped — every unmappable row lands here with its raw data and a reason.
-- ---------------------------------------------------------------------------

create table app.rule_import_unresolved (
  id                   uuid primary key default gen_random_uuid(),
  appcoll_task_type_id text,
  reason               text not null,
  raw                  jsonb not null,        -- the original CSV row, verbatim
  resolved             boolean not null default false,
  resolved_rule_id     uuid,                  -- set when a human maps it to a created rule
  created_at           timestamptz not null default now(),
  resolved_at          timestamptz
);

create index rule_import_unresolved_open_idx
  on app.rule_import_unresolved (created_at) where not resolved;

-- ---------------------------------------------------------------------------
-- A18 ladder stubs: reminder-pair task types (e.g. renewal reminder at 108 months paired with
-- the deadline at 120) do NOT survive as standalone rules — they become A18 ladder definitions
-- (WP 6.12). Import records them here as stubs instead of creating reminder rules.
-- ---------------------------------------------------------------------------

create table app.a18_ladder_stubs (
  id                   uuid primary key default gen_random_uuid(),
  appcoll_task_type_id text,
  reminder_of          text not null,         -- the deadline task type this reminder pairs with
  raw                  jsonb not null,
  created_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- RLS (D43: every table ships its policy in the same migration)
-- ---------------------------------------------------------------------------

alter table app.rule_import_unresolved enable row level security;
alter table app.rule_import_unresolved force row level security;
alter table app.a18_ladder_stubs       enable row level security;
alter table app.a18_ladder_stubs       force row level security;

-- Rule authoring/import/approval stays gated to the permissions admin (the migration 0007 note:
-- "no rule silently changes"). These queues are import by-products — same gate.
create policy rule_unresolved_admin on app.rule_import_unresolved
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());
create policy a18_stub_admin on app.a18_ladder_stubs
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

grant select, insert, update, delete on app.rule_import_unresolved, app.a18_ladder_stubs
  to authenticated;
