-- Migration 0011 — orchestrator (A0) core (WP 6.1).
--
-- The control plane every automated agent acts through (spec §12 A0). Three concerns:
--   * agent registry (ops.agents) — who may run, what actions/secrets they may touch, kill switch;
--   * approval queue (app.proposed_actions) — the human gate on anything sent/filed/invoiced/
--     instructed/posted (spec principle); approve = execute;
--   * egress log (ops.egress_log) — the audit answer to "what data left the building".
--
-- Registry + egress log are platform (ops) tables operated by system identities. The approval
-- queue is user-facing and RLS-scoped: a proposal on a matter follows that matter's visibility, so
-- an approver only sees proposals for matters they can see (D39/family ACLs).

-- ---------------------------------------------------------------------------
-- Agent registry (ops — system-managed)
-- ---------------------------------------------------------------------------

create table ops.agents (
  name                text primary key,          -- 'cipo-watcher', 'a9-drafter', …
  purpose             text not null,
  enabled             boolean not null default true,     -- kill switch (effective next action)
  allowed_actions     text[] not null default '{}',      -- action_types this agent may propose
  allowed_secret_slots text[] not null default '{}',     -- Bitwarden slots the broker may fetch
  created_at          timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Approval queue (app — user-facing, RLS-scoped)
-- ---------------------------------------------------------------------------

create type app.proposed_action_status as enum (
  'proposed', 'approved', 'rejected', 'expired', 'executed', 'failed'
);

create table app.proposed_actions (
  id            uuid primary key default gen_random_uuid(),
  agent_name    text not null,
  action_type   text not null,               -- 'email.send', 'task.create', 'filing.stage', …
  matter_id     uuid references app.matters (id),   -- null = firm-general proposal
  family_id     uuid references app.families (id),
  payload       jsonb not null default '{}'::jsonb, -- the draft/content/form the human reviews
  confidence    numeric,
  status        app.proposed_action_status not null default 'proposed',
  expires_at    timestamptz,
  proposed_at   timestamptz not null default now(),
  decided_by    uuid references app.os_users (id),
  decided_at    timestamptz,
  outcome       jsonb                         -- execution result recorded on approve
);

create index proposed_actions_open_idx on app.proposed_actions (proposed_at)
  where status = 'proposed';
create index proposed_actions_matter_idx on app.proposed_actions (matter_id);

-- ---------------------------------------------------------------------------
-- Egress log (ops — the confidentiality audit trail, D8/D45)
-- ---------------------------------------------------------------------------

create table ops.egress_log (
  id            bigint generated always as identity primary key,
  agent_name    text,
  action_class  text not null,               -- 'llm', 'email', 'webhook', 'api'
  destination   text not null,
  approval_ref  uuid,                         -- app.proposed_actions.id when gated
  redaction_ref text,                         -- redaction audit id for llm egress (D45)
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table app.proposed_actions enable row level security;
alter table app.proposed_actions force row level security;

-- A proposal is visible when it is firm-general (no matter) or its matter is visible to the caller.
create policy proposed_actions_select on app.proposed_actions
  for select using (
    (matter_id is null and app.is_active_staff()) or app.can_see_matter(matter_id)
  );
-- Staff (or a system worker on their behalf) enqueue proposals for visible matters.
create policy proposed_actions_insert on app.proposed_actions
  for insert with check (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  );
-- Approving/rejecting = an update, gated to staff who can see the matter.
create policy proposed_actions_update on app.proposed_actions
  for update using (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  ) with check (
    app.is_active_staff() and (matter_id is null or app.can_see_matter(matter_id))
  );

grant select, insert, update on app.proposed_actions to authenticated;

-- ---------------------------------------------------------------------------
-- Seed a demo agent so the registry + broker have something to exercise.
-- ---------------------------------------------------------------------------

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'demo-agent', 'WP 6.1 orchestrator demo agent',
  array['demo.action', 'email.send'], array['demo/api-key']
) on conflict (name) do nothing;

-- The registry is a read reference on the user path (propose_action checks the allow-list); the
-- broker's secret allow-list is also read here. Not user-RLS (a firm-wide registry), just granted.
-- The user role needs schema USAGE on ops to reach the table (events/0002 only touched it as the
-- system role).
grant usage on schema ops to authenticated;
grant select on ops.agents to authenticated;
