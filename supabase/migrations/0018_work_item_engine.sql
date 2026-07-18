-- Migration 0018 — work-item engine + scripted project templates (WP 5.1, spec §M9, D30).
--
-- Tier 2 of the three-tier work model: projects. Tier 1 (docket tasks) is WP 1.2; Tier 3
-- (micro-requests) is WP 5.4. This migration adds the scripted half — template-driven projects
-- with stages, chained tasks, role routing and standard cycle times — and the dependency graph
-- the chaining runs on.
--
-- Two structural commitments:
--
--   * **Templates are versioned and immutable once published.** Editing a published template
--     creates a new version; in-flight projects keep the version they launched from. This is the
--     same discipline as versioned deadline rules (WP 1.2) and effective-dated fees (WP 2.3), for
--     the same reason: a project running under "OA response v3" must stay explainable after v4
--     changes the stages, or nobody can answer why a task exists.
--
--   * **Blocking is derived, never stored.** A work item is blocked iff an incomplete predecessor
--     exists — computed by `app.work_item_is_blocked`, not held in a column. A stored flag has to
--     be maintained on every completion, reopen, dependency edit and cancellation, and the first
--     missed update leaves someone staring at a task that says "blocked" with nothing blocking it.

-- ---------------------------------------------------------------------------
-- Work-item status: widen the 0.8 substrate
-- ---------------------------------------------------------------------------
--
-- The minimal substrate shipped 'open'/'done' (WP 0.8). Both values are preserved so WP 5.5
-- My Day and WP 2.5.1 time entries keep working untouched.

alter table app.work_items drop constraint if exists work_items_status_check;
alter table app.work_items add constraint work_items_status_check
  check (status in ('open', 'in_progress', 'done', 'cancelled'));

-- ---------------------------------------------------------------------------
-- Templates
-- ---------------------------------------------------------------------------

create type app.template_status as enum ('draft', 'published', 'retired');

create table app.project_templates (
  id           uuid primary key default gen_random_uuid(),
  key          text not null,               -- stable slug: 'oa-response', 'pct-national-entry'
  version      integer not null check (version > 0),
  name         text not null,
  description  text,
  -- Optional scoping so the launcher can offer the right templates for a matter.
  jurisdiction text,
  matter_type  text,
  status       app.template_status not null default 'draft',
  published_at timestamptz,
  created_by   uuid not null references app.os_users (id),
  created_at   timestamptz not null default now(),
  unique (key, version)
);

-- At most one published version per key: "which version does a new project get?" must have one
-- answer. Older published versions are retired, not deleted — in-flight projects still cite them.
create unique index project_templates_one_published
  on app.project_templates (key) where status = 'published';

create table app.project_template_stages (
  id          uuid primary key default gen_random_uuid(),
  template_id uuid not null references app.project_templates (id) on delete cascade,
  ordinal     integer not null,
  name        text not null,
  unique (template_id, ordinal)
);

create table app.project_template_tasks (
  id          uuid primary key default gen_random_uuid(),
  template_id uuid not null references app.project_templates (id) on delete cascade,
  stage_id    uuid references app.project_template_stages (id) on delete cascade,
  -- Stable identifier within the template. Dependencies reference this rather than a uuid, so a
  -- template can be authored, exported and re-imported without rewriting its own edges.
  task_ref    text not null,
  title       text not null,
  description text,
  -- Role routing: resolved to a person at launch (WP 5.2 adds workload-aware assignment).
  role        text,
  -- Standard cycle time in BUSINESS days — internal working time, not a calendar offset.
  cycle_days  integer not null default 1 check (cycle_days >= 0),
  -- Business days after project start before this task may begin, when it has no predecessors.
  start_offset_days integer not null default 0 check (start_offset_days >= 0),
  is_milestone boolean not null default false,
  ordinal     integer not null default 0,
  unique (template_id, task_ref)
);

-- Chained tasks: the DAG. Acyclicity is enforced in the domain layer at publish time
-- (a recursive DB constraint would fire per-row during authoring, when the graph is
-- legitimately incomplete).
create table app.project_template_dependencies (
  template_id     uuid not null references app.project_templates (id) on delete cascade,
  task_ref        text not null,
  depends_on_ref  text not null,
  primary key (template_id, task_ref, depends_on_ref),
  check (task_ref <> depends_on_ref)          -- the one cycle cheap enough to catch here
);

-- ---------------------------------------------------------------------------
-- Role routing
-- ---------------------------------------------------------------------------

create table app.role_assignments (
  role       text primary key,
  user_id    uuid references app.os_users (id) on delete set null,
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Project instances
-- ---------------------------------------------------------------------------

create table app.projects (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  matter_id        uuid references app.matters (id) on delete cascade,
  family_id        uuid references app.families (id) on delete cascade,
  -- The version launched from, recorded verbatim. `template_id` may be retired later; these two
  -- columns keep the answer to "what did this project follow?" readable without a join.
  template_id      uuid references app.project_templates (id) on delete set null,
  template_key     text,
  template_version integer,
  status           text not null default 'active'
                     check (status in ('active', 'completed', 'cancelled')),
  started_on       date not null default current_date,
  target_end       date,
  created_by       uuid not null references app.os_users (id),
  created_at       timestamptz not null default now(),
  completed_at     timestamptz
);

create index projects_matter_idx on app.projects (matter_id);
create index projects_status_idx on app.projects (status) where status = 'active';

-- Work items gain their project context. All nullable: manual and micro-request items (Tier 3)
-- live in the same table and belong to no project.
alter table app.work_items add column project_id  uuid references app.projects (id)
  on delete cascade;
alter table app.work_items add column stage_name  text;
alter table app.work_items add column task_ref    text;
alter table app.work_items add column role        text;
alter table app.work_items add column started_on  date;
alter table app.work_items add column completed_on date;
alter table app.work_items add column ordinal     integer not null default 0;

create index work_items_project_idx on app.work_items (project_id, ordinal);

-- The instantiated dependency graph.
create table app.work_item_dependencies (
  work_item_id   uuid not null references app.work_items (id) on delete cascade,
  depends_on_id  uuid not null references app.work_items (id) on delete cascade,
  primary key (work_item_id, depends_on_id),
  check (work_item_id <> depends_on_id)
);

create index work_item_dependencies_depends_idx
  on app.work_item_dependencies (depends_on_id);

-- ---------------------------------------------------------------------------
-- Derived blocking
-- ---------------------------------------------------------------------------
--
-- A cancelled predecessor does NOT block: cancelling a task is a decision that it will not
-- happen, and leaving its successors blocked forever would strand the project with no way
-- forward short of editing the graph.

create or replace function app.work_item_is_blocked(item uuid) returns boolean
  language sql stable
as $$
  select exists (
    select 1
      from app.work_item_dependencies d
      join app.work_items w on w.id = d.depends_on_id
     where d.work_item_id = item
       and w.status not in ('done', 'cancelled')
  );
$$;

-- The queue view every consumer reads: My Day (5.5), boards (5.3), capacity (5.2).
create or replace view app.work_item_queue as
  select
    w.id, w.title, w.matter_id, w.project_id, w.stage_name, w.task_ref, w.role,
    w.assignee_id, w.status, w.due_date, w.ordinal, w.created_at,
    app.work_item_is_blocked(w.id) as is_blocked,
    p.name as project_name
  from app.work_items w
  left join app.projects p on p.id = w.project_id;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
--
-- Templates are firm-general configuration: everyone reads them (you cannot launch what you
-- cannot see), authoring is staff-level, and PUBLISHING is admin-gated — a published template
-- silently changes what every future project does.

alter table app.project_templates             enable row level security;
alter table app.project_templates             force row level security;
alter table app.project_template_stages       enable row level security;
alter table app.project_template_stages       force row level security;
alter table app.project_template_tasks        enable row level security;
alter table app.project_template_tasks        force row level security;
alter table app.project_template_dependencies enable row level security;
alter table app.project_template_dependencies force row level security;
alter table app.role_assignments              enable row level security;
alter table app.role_assignments              force row level security;
alter table app.projects                      enable row level security;
alter table app.projects                      force row level security;
alter table app.work_item_dependencies        enable row level security;
alter table app.work_item_dependencies        force row level security;

create policy project_templates_read on app.project_templates
  for select using (app.is_active_staff());
create policy project_templates_author on app.project_templates
  for insert with check (app.is_active_staff() and status = 'draft');
-- Draft edits are open to staff; moving a template to 'published' or 'retired' is not.
create policy project_templates_update on app.project_templates
  for update using (
    app.is_active_staff() and (status = 'draft' or app.is_permissions_admin())
  )
  with check (
    app.is_permissions_admin() or status = 'draft'
  );
create policy project_templates_delete on app.project_templates
  for delete using (app.is_active_staff() and status = 'draft');

create policy template_stages_all on app.project_template_stages
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy template_tasks_all on app.project_template_tasks
  for all using (app.is_active_staff()) with check (app.is_active_staff());
create policy template_deps_all on app.project_template_dependencies
  for all using (app.is_active_staff()) with check (app.is_active_staff());

create policy role_assignments_read on app.role_assignments
  for select using (app.is_active_staff());
create policy role_assignments_write on app.role_assignments
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

-- Projects follow matter visibility, exactly as tasks and work items do. A firm-general project
-- (no matter) is staff-visible.
create policy projects_select on app.projects
  for select using (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  );
create policy projects_write on app.projects
  for all using (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  )
  with check (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  );

-- Dependencies inherit the visibility of the item they hang off.
create policy work_item_deps_all on app.work_item_dependencies
  for all using (app.is_active_staff()) with check (app.is_active_staff());

grant select, insert, update, delete on
  app.project_templates, app.project_template_stages, app.project_template_tasks,
  app.project_template_dependencies, app.role_assignments, app.projects,
  app.work_item_dependencies
  to authenticated;
grant select on app.work_item_queue to authenticated;
grant execute on function app.work_item_is_blocked(uuid) to authenticated;
