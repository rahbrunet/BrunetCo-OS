-- Migration 0020 — board framework (WP 5.3, spec §M9, D30).
--
-- The Monday-model layer, native and matter-linked. A board is NOT a container that owns items —
-- it is a configurable LENS over the work items that already exist (docket tasks from M1, project
-- tasks from 5.1, manual items). Membership is derived from the board's scope, so a task shows up
-- on the firm board, its matter board and its project board at once without being copied three
-- times and drifting between them. This is the same "derived, not stored" discipline as 5.1's
-- blocking and 5.2's workload.
--
-- What a board DOES own: its column configuration, its saved views, its groups, and its
-- automation rules. Those are presentation and behaviour, not the work itself.
--
-- Custom column *values* are the one thing that must be stored per item (there is nowhere else to
-- put "the estimated page count for this OA response"), and they are keyed to the column so a
-- column's deletion takes its values with it.

-- ---------------------------------------------------------------------------
-- Boards (scope-based lenses)
-- ---------------------------------------------------------------------------

create type app.board_scope as enum ('firm', 'matter', 'project');

create table app.boards (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  scope_type  app.board_scope not null,
  -- The matter or project this board lenses. Null for a firm-wide board.
  scope_id    uuid,
  created_by  uuid not null references app.os_users (id),
  created_at  timestamptz not null default now(),
  -- A matter/project board must name its scope; a firm board must not.
  check ((scope_type = 'firm') = (scope_id is null))
);

create index boards_scope_idx on app.boards (scope_type, scope_id);

-- ---------------------------------------------------------------------------
-- Typed columns
-- ---------------------------------------------------------------------------
--
-- Builtin columns project existing work_item fields (status, owner, due, priority, matter, stage)
-- and carry no per-item storage — they read through to the item. Custom columns are user-defined
-- typed fields whose values live in work_item_field_values.

create type app.column_type as enum (
  'text', 'number', 'date', 'single_select', 'status', 'person', 'checkbox'
);

create table app.board_columns (
  id          uuid primary key default gen_random_uuid(),
  board_id    uuid not null references app.boards (id) on delete cascade,
  key         text not null,               -- 'status', 'owner', or a custom slug
  label       text not null,
  col_type    app.column_type not null,
  is_builtin  boolean not null default false,
  -- For single_select/status: {"options": [{"value": "...", "colour": "..."}]}. Free-form
  -- otherwise. The domain layer validates values against this.
  config      jsonb not null default '{}'::jsonb,
  ordinal     integer not null default 0,
  unique (board_id, key)
);

create index board_columns_board_idx on app.board_columns (board_id, ordinal);

-- Custom column values. Builtin columns never appear here — they read through to work_items.
create table app.work_item_field_values (
  work_item_id uuid not null references app.work_items (id) on delete cascade,
  column_id    uuid not null references app.board_columns (id) on delete cascade,
  value        jsonb,
  updated_at   timestamptz not null default now(),
  primary key (work_item_id, column_id)
);

-- ---------------------------------------------------------------------------
-- Groups (board-local partitions of items)
-- ---------------------------------------------------------------------------

create table app.board_groups (
  id       uuid primary key default gen_random_uuid(),
  board_id uuid not null references app.boards (id) on delete cascade,
  name     text not null,
  colour   text,
  ordinal  integer not null default 0
);

create index board_groups_board_idx on app.board_groups (board_id, ordinal);

create table app.board_item_groups (
  board_id     uuid not null references app.boards (id) on delete cascade,
  work_item_id uuid not null references app.work_items (id) on delete cascade,
  group_id     uuid not null references app.board_groups (id) on delete cascade,
  primary key (board_id, work_item_id)
);

-- ---------------------------------------------------------------------------
-- Saved views
-- ---------------------------------------------------------------------------
--
-- The five view types are renderings of the same data; the backend stores the config (filter,
-- sort, grouping), the frontend draws. A saved view is also a saved filter — the spec lists both
-- and they are the same object.

create type app.board_view_type as enum (
  'table', 'kanban', 'timeline', 'calendar', 'workload'
);

create table app.board_views (
  id         uuid primary key default gen_random_uuid(),
  board_id   uuid not null references app.boards (id) on delete cascade,
  name       text not null,
  view_type  app.board_view_type not null default 'table',
  -- {"filter": {...}, "sort": [...], "group_by": "status"}. Interpreted by the frontend; the
  -- domain layer validates its shape so a malformed saved view cannot brick the board.
  config     jsonb not null default '{}'::jsonb,
  is_default boolean not null default false,
  created_by uuid not null references app.os_users (id),
  created_at timestamptz not null default now()
);

create index board_views_board_idx on app.board_views (board_id);
-- At most one default view per board — "which view opens?" needs one answer.
create unique index board_views_one_default on app.board_views (board_id) where is_default;

-- ---------------------------------------------------------------------------
-- Automations (no-code rules)
-- ---------------------------------------------------------------------------
--
-- "When status -> Filed, create an invoice-review task; when review approved, notify owner."
-- A rule is a trigger + ordered actions, both stored as validated JSON. The engine
-- (domain/boards.py) matches a work-item event against triggers and executes actions; every run
-- is logged so a surprising side effect is traceable to the rule that caused it.

create table app.board_automations (
  id         uuid primary key default gen_random_uuid(),
  board_id   uuid not null references app.boards (id) on delete cascade,
  name       text not null,
  -- {"event": "status_changed", "to": "done"} or {"event": "field_changed", "column": "...", ...}
  trigger    jsonb not null,
  -- [{"type": "create_task", "title": "...", "role": "..."}, {"type": "notify", "target": "owner"}]
  actions    jsonb not null,
  enabled    boolean not null default true,
  created_by uuid not null references app.os_users (id),
  created_at timestamptz not null default now()
);

create index board_automations_board_idx on app.board_automations (board_id) where enabled;

create table app.board_automation_runs (
  id            uuid primary key default gen_random_uuid(),
  automation_id uuid references app.board_automations (id) on delete set null,
  work_item_id  uuid references app.work_items (id) on delete set null,
  matched       boolean not null,
  actions_taken jsonb not null default '[]'::jsonb,
  detail        text,
  ran_at        timestamptz not null default now()
);

create index board_automation_runs_automation_idx
  on app.board_automation_runs (automation_id, ran_at desc);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- Boards are firm-general operational configuration. A matter- or project-scoped board is only
-- meaningful to someone who can see that matter, so its VISIBILITY follows the scope; a firm board
-- is staff-visible. Field values follow the visibility of the work item they annotate — a custom
-- field on a restricted matter's task must not leak through a board.

create or replace function app.can_see_board(bid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.boards b
     where b.id = bid
       and app.is_active_staff()
       and (
         b.scope_type = 'firm'
         or (b.scope_type = 'matter' and app.can_see_matter(b.scope_id))
         or (b.scope_type = 'project' and exists (
              select 1 from app.projects p
               where p.id = b.scope_id
                 and (p.matter_id is null or app.can_see_matter(p.matter_id))
            ))
       )
  );
$$;

revoke all on function app.can_see_board(uuid) from public;
grant execute on function app.can_see_board(uuid) to authenticated;

alter table app.boards                  enable row level security;
alter table app.boards                  force row level security;
alter table app.board_columns           enable row level security;
alter table app.board_columns           force row level security;
alter table app.work_item_field_values  enable row level security;
alter table app.work_item_field_values  force row level security;
alter table app.board_groups            enable row level security;
alter table app.board_groups            force row level security;
alter table app.board_item_groups       enable row level security;
alter table app.board_item_groups       force row level security;
alter table app.board_views             enable row level security;
alter table app.board_views             force row level security;
alter table app.board_automations       enable row level security;
alter table app.board_automations       force row level security;
alter table app.board_automation_runs   enable row level security;
alter table app.board_automation_runs   force row level security;

create policy boards_select on app.boards for select using (app.can_see_board(id));
create policy boards_write on app.boards
  for all using (app.is_active_staff() and (scope_id is null or app.can_see_board(id)))
  with check (
    app.is_active_staff()
    and (
      scope_type = 'firm'
      or (scope_type = 'matter' and app.can_see_matter(scope_id))
      or (scope_type = 'project' and exists (
           select 1 from app.projects p where p.id = scope_id
             and (p.matter_id is null or app.can_see_matter(p.matter_id))
         ))
    )
  );

-- Board-owned config tables all inherit board visibility.
create policy board_columns_all on app.board_columns
  for all using (app.can_see_board(board_id)) with check (app.can_see_board(board_id));
create policy board_groups_all on app.board_groups
  for all using (app.can_see_board(board_id)) with check (app.can_see_board(board_id));
create policy board_item_groups_all on app.board_item_groups
  for all using (app.can_see_board(board_id)) with check (app.can_see_board(board_id));
create policy board_views_all on app.board_views
  for all using (app.can_see_board(board_id)) with check (app.can_see_board(board_id));
create policy board_automations_all on app.board_automations
  for all using (app.can_see_board(board_id)) with check (app.can_see_board(board_id));
create policy board_automation_runs_select on app.board_automation_runs
  for select using (
    automation_id is null or exists (
      select 1 from app.board_automations a
       where a.id = automation_id and app.can_see_board(a.board_id)
    )
  );

-- Field values follow the WORK ITEM's visibility, not just the board's: the same custom column
-- may carry a value on a restricted matter's item, and that value is as sensitive as the item.
create policy work_item_field_values_all on app.work_item_field_values
  for all using (
    exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
         and app.is_active_staff()
    )
  )
  with check (
    exists (
      select 1 from app.work_items w
       where w.id = work_item_id
         and (w.matter_id is null or app.can_see_matter(w.matter_id))
         and app.is_active_staff()
    )
  );

grant select, insert, update, delete on
  app.boards, app.board_columns, app.work_item_field_values, app.board_groups,
  app.board_item_groups, app.board_views, app.board_automations
  to authenticated;
grant select on app.board_automation_runs to authenticated;
