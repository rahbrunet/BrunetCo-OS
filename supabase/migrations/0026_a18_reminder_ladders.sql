-- Migration 0026 — A18 reminder & follow-up ladders (WP 6.12, spec §A18, D31).
--
-- The firm's chasing engine. A *ladder* is a configured escalating sequence of reminders for a
-- (kind, task type, jurisdiction) combination; a *schedule* is one live run of that ladder against
-- one task or work item; a *send* is one rendered rung of that run.
--
-- Three rules from D31 are structural here, not conventions the caller has to remember:
--
--   1. Silence never abandons. A schedule whose last rung passes with no response does NOT close.
--      It moves to 'exhausted' and — when the ladder is rights-preserving — an escalation row with
--      an explicit pay-or-abandon decision is opened for the responsible professional. The decision
--      starts 'pending' and someone has to record it. Nothing in this schema lets a maintenance fee
--      lapse by default.
--   2. Review-first sending. Every send is brokered through the WP 6.1 approval queue
--      (app.proposed_actions). `auto_remind` on app.client_reminder_prefs is the dormant
--      auto-send flag from D31's revision — per client and per task type, defaulting to FALSE — so
--      turning automation on later is a configuration change, not a rebuild.
--   3. Halting is a first-class event, with a reason. An instruction arriving (A8 detects a reply,
--      or someone logs it manually) stops the ladder and the reason survives on the row, because
--      "why did we stop chasing this?" is the question that gets asked six months later.
--
-- Offsets are days relative to the schedule's anchor date and are configured, never hard-coded:
-- the canonical maintenance-fee ladder (T−60 courtesy, T−30 action requested, T−14 FINAL REMINDER)
-- is three rows in app.reminder_ladder_steps, not three branches in the engine.

-- ---------------------------------------------------------------------------
-- Task typing — the key ladders match on
-- ---------------------------------------------------------------------------
--
-- Rules and the AppColl import already speak in task types (app.a18_ladder_stubs.reminder_of is
-- one), but app.tasks had nowhere to carry the type forward, so a generated task could not be
-- matched back to its ladder. Nullable: manual tasks and pre-6.12 rows have no type and simply
-- never auto-start a ladder.
alter table app.tasks add column task_type text;
create index tasks_task_type_idx on app.tasks (task_type) where status = 'open';

-- ---------------------------------------------------------------------------
-- Ladder definitions
-- ---------------------------------------------------------------------------

create type app.reminder_ladder_kind as enum (
  'deadline',        -- (a) rungs count BACKWARD from a deadline: offsets are negative
  'awaiting_client'  -- (b) rungs count FORWARD from the awaiting-client tag: offsets are positive
);

create table app.reminder_ladders (
  id                uuid primary key default gen_random_uuid(),
  kind              app.reminder_ladder_kind not null,
  name              text not null,
  task_type         text not null,          -- matches app.tasks.task_type
  jurisdiction_code text,                   -- null = any jurisdiction (the fallback ladder)
  -- Drives the exhaustion path. True for maintenance fees, deadline responses, anything where
  -- doing nothing costs the client a legal right — those escalate for a documented decision and
  -- are exempt from stop-work (6.13) unless the principal signs off.
  rights_preserving boolean not null default false,
  active            boolean not null default true,
  created_by        uuid not null references app.os_users (id),
  created_at        timestamptz not null default now()
);

-- One ladder per (kind, task type, jurisdiction). The null-jurisdiction row is the fallback and
-- coexists with jurisdiction-specific rows, so the partial unique indexes are split.
create unique index reminder_ladders_specific_key
  on app.reminder_ladders (kind, task_type, jurisdiction_code)
  where active and jurisdiction_code is not null;
create unique index reminder_ladders_fallback_key
  on app.reminder_ladders (kind, task_type)
  where active and jurisdiction_code is null;

create table app.reminder_ladder_steps (
  ladder_id   uuid not null references app.reminder_ladders (id) on delete cascade,
  step_no     int  not null check (step_no >= 1),
  -- Days from the anchor. Negative = before (deadline ladders); positive = after (follow-ups).
  offset_days int  not null,
  label       text not null,               -- 'courtesy', 'action requested', 'FINAL REMINDER'
  subject     text not null,               -- {placeholder} templates; rendered per send
  body        text not null,
  primary key (ladder_id, step_no)
);

-- ---------------------------------------------------------------------------
-- Client preferences — the dormant auto-send flag + suppression
-- ---------------------------------------------------------------------------

create table app.client_reminder_prefs (
  client_id    uuid not null references app.clients (id) on delete cascade,
  task_type    text not null default '',   -- '' = the client-wide default row
  -- D31 (revised): preserved but dormant. Even when true, AI-composed content still requires
  -- review — the send-mode resolution in py_shared.domain.reminders enforces that, not this flag.
  auto_remind  boolean not null default false,
  -- Suppression is absolute and beats auto_remind: an unsubscribed contact is never chased.
  unsubscribed boolean not null default false,
  updated_at   timestamptz not null default now(),
  primary key (client_id, task_type)
);

-- ---------------------------------------------------------------------------
-- Live schedules
-- ---------------------------------------------------------------------------

create type app.reminder_schedule_status as enum (
  'active',     -- rungs still to send
  'halted',     -- an instruction/reply arrived; the ladder stopped on purpose (reason recorded)
  'exhausted',  -- every rung sent, nothing came back — NEVER a silent close
  'escalated',  -- exhausted + handed to a professional for a pay-or-abandon decision
  'cancelled'   -- the underlying task closed, or an admin stood it down
);

create table app.reminder_schedules (
  id            uuid primary key default gen_random_uuid(),
  ladder_id     uuid not null references app.reminder_ladders (id),
  matter_id     uuid not null references app.matters (id),
  task_id       uuid references app.tasks (id) on delete cascade,
  work_item_id  uuid references app.work_items (id) on delete cascade,
  -- Deadline ladders anchor on the task's final due date; follow-ups on the tag date.
  anchor_date   date not null,
  status        app.reminder_schedule_status not null default 'active',
  halted_reason text,
  halted_at     timestamptz,
  created_at    timestamptz not null default now(),
  -- Exactly one subject. A ladder chasing nothing, or two things, is a caller bug.
  check (num_nonnulls(task_id, work_item_id) = 1),
  -- A halted schedule must say why; anything else must not claim a halt reason.
  check ((status = 'halted') = (halted_reason is not null))
);

-- One live ladder per subject: re-running the sweep, or two triggers firing, must not double-chase
-- the same client. History is unconstrained, so a re-opened task can be chased again later.
create unique index reminder_schedules_one_active_task
  on app.reminder_schedules (task_id) where status = 'active' and task_id is not null;
create unique index reminder_schedules_one_active_item
  on app.reminder_schedules (work_item_id) where status = 'active' and work_item_id is not null;
create index reminder_schedules_matter_idx on app.reminder_schedules (matter_id);
create index reminder_schedules_active_idx on app.reminder_schedules (anchor_date)
  where status = 'active';

-- ---------------------------------------------------------------------------
-- Sends — one row per rung, materialised when the rung comes due
-- ---------------------------------------------------------------------------

create type app.reminder_send_status as enum (
  'queued',      -- rendered and sitting in the approval queue awaiting a human (the v1 path)
  'sent',        -- approved and handed to transport
  'failed',      -- transport rejected it
  'suppressed'   -- the client is unsubscribed, or the matter is exempt; recorded, never sent
);

create table app.reminder_sends (
  id                 uuid primary key default gen_random_uuid(),
  schedule_id        uuid not null references app.reminder_schedules (id) on delete cascade,
  step_no            int  not null,
  due_on             date not null,               -- anchor + step offset
  status             app.reminder_send_status not null default 'queued',
  -- The approval-queue row a human acts on. Null only for a suppressed rung, which never queues.
  proposed_action_id uuid references app.proposed_actions (id),
  subject            text not null,               -- rendered at materialisation, not at send
  body               text not null,
  -- False only when the dormant auto_remind path is enabled for deterministic template content.
  review_required    boolean not null default true,
  suppressed_reason  text,
  sent_at            timestamptz,
  delivery_status    text,                        -- transport's word: 'delivered', 'bounced', …
  created_at         timestamptz not null default now(),
  -- A rung is materialised exactly once per schedule; the sweep is therefore idempotent.
  unique (schedule_id, step_no)
);

create index reminder_sends_schedule_idx on app.reminder_sends (schedule_id, step_no);

-- ---------------------------------------------------------------------------
-- Exhaustion escalation — the "silence never abandons" record
-- ---------------------------------------------------------------------------

create type app.reminder_decision as enum ('pending', 'pay', 'abandon', 'other');

create table app.reminder_escalations (
  id           uuid primary key default gen_random_uuid(),
  schedule_id  uuid not null unique references app.reminder_schedules (id) on delete cascade,
  escalated_to uuid references app.os_users (id) on delete set null,
  -- Starts 'pending' and stays there until a human records the call. The pending set IS the
  -- "nothing lapsed by default" report.
  decision     app.reminder_decision not null default 'pending',
  note         text,
  decided_by   uuid references app.os_users (id),
  decided_at   timestamptz,
  created_at   timestamptz not null default now(),
  -- A recorded decision must carry who and when; a pending one must not pretend to.
  check ((decision = 'pending') = (decided_at is null)),
  check ((decided_at is null) = (decided_by is null))
);

create index reminder_escalations_pending_idx on app.reminder_escalations (created_at)
  where decision = 'pending';

-- ---------------------------------------------------------------------------
-- Agent registry (D10/WP 6.1)
-- ---------------------------------------------------------------------------
--
-- A18 proposes 'reminder.send' and nothing else: it drafts and queues, a human sends. No secret
-- slots — it reaches no external service directly; transport is the approved action's handler.

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'a18-reminder',
  'A18 — escalating reminder & follow-up ladders; queues every send for human review (WP 6.12)',
  array['reminder.send']::text[],
  array[]::text[]
) on conflict (name) do nothing;

-- ---------------------------------------------------------------------------
-- RLS (D43: every table ships its policy in the same migration)
-- ---------------------------------------------------------------------------
--
-- Ladder definitions and client preferences are configuration that changes what the firm says to
-- clients under its own name — same gate as docket rules: readable by staff, writable only by the
-- permissions admin. Live schedules, sends and escalations follow matter visibility, so a ladder
-- on a matter you cannot see is a matter you cannot see.

alter table app.reminder_ladders       enable row level security;
alter table app.reminder_ladders       force row level security;
alter table app.reminder_ladder_steps  enable row level security;
alter table app.reminder_ladder_steps  force row level security;
alter table app.client_reminder_prefs  enable row level security;
alter table app.client_reminder_prefs  force row level security;
alter table app.reminder_schedules     enable row level security;
alter table app.reminder_schedules     force row level security;
alter table app.reminder_sends         enable row level security;
alter table app.reminder_sends         force row level security;
alter table app.reminder_escalations   enable row level security;
alter table app.reminder_escalations   force row level security;

create policy reminder_ladders_read on app.reminder_ladders
  for select using (app.is_active_staff());
create policy reminder_ladders_admin on app.reminder_ladders
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy reminder_steps_read on app.reminder_ladder_steps
  for select using (app.is_active_staff());
create policy reminder_steps_admin on app.reminder_ladder_steps
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy reminder_prefs_read on app.client_reminder_prefs
  for select using (app.is_active_staff());
create policy reminder_prefs_admin on app.client_reminder_prefs
  for all using (app.is_permissions_admin()) with check (app.is_permissions_admin());

create policy reminder_schedules_select on app.reminder_schedules
  for select using (app.can_see_matter(matter_id));
create policy reminder_schedules_insert on app.reminder_schedules
  for insert with check (app.can_see_matter(matter_id) and app.is_active_staff());
create policy reminder_schedules_update on app.reminder_schedules
  for update using (app.can_see_matter(matter_id) and app.is_active_staff())
  with check (app.can_see_matter(matter_id));

create or replace function app.can_see_reminder_schedule(sid uuid) returns boolean
  language sql stable security definer set search_path = app, public
as $$
  select exists (
    select 1 from app.reminder_schedules s
     where s.id = sid and app.can_see_matter(s.matter_id)
  );
$$;

revoke all on function app.can_see_reminder_schedule(uuid) from public;
grant execute on function app.can_see_reminder_schedule(uuid) to authenticated;

create policy reminder_sends_select on app.reminder_sends
  for select using (app.can_see_reminder_schedule(schedule_id));
create policy reminder_sends_write on app.reminder_sends
  for insert with check (app.can_see_reminder_schedule(schedule_id) and app.is_active_staff());
create policy reminder_sends_update on app.reminder_sends
  for update using (app.can_see_reminder_schedule(schedule_id) and app.is_active_staff())
  with check (app.can_see_reminder_schedule(schedule_id));

create policy reminder_escalations_select on app.reminder_escalations
  for select using (app.can_see_reminder_schedule(schedule_id));
create policy reminder_escalations_write on app.reminder_escalations
  for insert with check (app.can_see_reminder_schedule(schedule_id) and app.is_active_staff());
create policy reminder_escalations_update on app.reminder_escalations
  for update using (app.can_see_reminder_schedule(schedule_id) and app.is_active_staff())
  with check (app.can_see_reminder_schedule(schedule_id));

grant select on app.reminder_ladders, app.reminder_ladder_steps, app.client_reminder_prefs
  to authenticated;
grant insert, update, delete on app.reminder_ladders, app.reminder_ladder_steps,
  app.client_reminder_prefs to authenticated;
grant select, insert, update on app.reminder_schedules, app.reminder_sends,
  app.reminder_escalations to authenticated;
