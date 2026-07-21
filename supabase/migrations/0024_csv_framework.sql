-- Migration 0024 — CSV import/export framework (WP 5B.2, spec §M9 supporting).
--
-- A shared import pipeline for every module that takes a spreadsheet: staged rows, per-row
-- validation, quarantine-not-abort, and a reconciliation count. The AppColl migration (WP 1.6)
-- has the same shape for the same reasons — this is the generic version every other module uses,
-- so no module hand-rolls its own half-safe CSV loader.
--
-- The discipline that matters: an import NEVER partially commits silently. Rows are staged and
-- validated first; a row that fails is quarantined with its raw payload and the reason, and the
-- run reports counts that must reconcile (seen = imported + rejected). "It looked like it worked"
-- is the failure mode a spreadsheet import must not have.

create type app.import_status as enum (
  'staged',     -- rows parsed and validated; nothing committed yet
  'committed',  -- valid rows applied
  'failed',     -- the file could not be processed at all (bad header, unreadable)
  'cancelled'
);

create table app.csv_imports (
  id            uuid primary key default gen_random_uuid(),
  entity        text not null,               -- registered handler key: 'clients', 'contacts', ...
  filename      text,
  uploaded_by   uuid not null references app.os_users (id),
  status        app.import_status not null default 'staged',
  rows_seen     integer not null default 0,
  rows_valid    integer not null default 0,
  rows_rejected integer not null default 0,
  rows_committed integer not null default 0,
  detail        text,
  created_at    timestamptz not null default now(),
  committed_at  timestamptz
);

create index csv_imports_entity_idx on app.csv_imports (entity, created_at desc);

create table app.csv_import_rows (
  id          uuid primary key default gen_random_uuid(),
  import_id   uuid not null references app.csv_imports (id) on delete cascade,
  row_number  integer not null,              -- 1-based, matching what the user sees in Excel
  raw         jsonb not null,                -- the row as read, preserved verbatim
  parsed      jsonb,                         -- typed values when the row validated
  error       text,                          -- null when valid
  committed   boolean not null default false,
  unique (import_id, row_number)
);

create index csv_import_rows_error_idx on app.csv_import_rows (import_id)
  where error is not null;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- An import is visible to the person who uploaded it and to staff generally (imports are an
-- operational activity the team can see), but a file's raw rows can contain anything the uploader
-- pasted in — so writes stay with the uploader.

alter table app.csv_imports     enable row level security;
alter table app.csv_imports     force row level security;
alter table app.csv_import_rows enable row level security;
alter table app.csv_import_rows force row level security;

create policy csv_imports_select on app.csv_imports
  for select using (app.is_active_staff());
create policy csv_imports_insert on app.csv_imports
  for insert with check (app.is_active_staff() and uploaded_by = app.jwt_uid());
create policy csv_imports_update on app.csv_imports
  for update using (uploaded_by = app.jwt_uid());

create policy csv_import_rows_select on app.csv_import_rows
  for select using (
    exists (select 1 from app.csv_imports i where i.id = import_id and app.is_active_staff())
  );
create policy csv_import_rows_write on app.csv_import_rows
  for all using (
    exists (select 1 from app.csv_imports i
             where i.id = import_id and i.uploaded_by = app.jwt_uid())
  )
  with check (
    exists (select 1 from app.csv_imports i
             where i.id = import_id and i.uploaded_by = app.jwt_uid())
  );

grant select, insert, update on app.csv_imports to authenticated;
grant select, insert, update, delete on app.csv_import_rows to authenticated;
