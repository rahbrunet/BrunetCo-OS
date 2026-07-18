-- Migration 0015 — shared redaction service + A9 email drafter (WP 6.9, spec §A9, D45).
--
-- Two concerns, deliberately separable because the first is a Track-A platform dependency and
-- the second is Track B:
--
--   * ops.redaction_events / ops.llm_egress_log — the D45 audit spine. EVERY external-LLM call
--     in the OS (A9 drafting, A10 quote intent, A8 classification, A11 OA reporting) redacts
--     first and logs here. The egress gate (WP 6.1) refuses an LLM action with no redaction ref,
--     so a row here is the evidence that the refusal did not have to fire.
--   * app.email_drafts / app.draft_style_examples — the A9 review queue and the per-user style
--     corpus. Both are per-user private (D39): a draft written from your mailbox, and the sent
--     mail that taught its voice, are yours. Neither is firm-visible.
--
-- Deliberately NOT stored: the redaction mapping itself (placeholder -> real value). Persisting
-- it would recreate, in a firm-general ops table, exactly the identifiers redaction exists to
-- keep out of external hands. The audit records label counts + the leak verdict, which is what
-- an auditor needs; the mapping lives only in process memory for the rehydrate round-trip.

-- ---------------------------------------------------------------------------
-- Redaction audit (ops — firm-general, staff-readable)
-- ---------------------------------------------------------------------------

create table ops.redaction_events (
  id             uuid primary key default gen_random_uuid(),
  ref            text not null unique,      -- the redaction reference the egress gate checks
  agent_name     text not null,
  backend        text not null,             -- NER backend identity ('spacy:en_core_web_md', ...)
  entity_counts  jsonb not null default '{}'::jsonb,  -- {PERSON: 4, ORG: 2, EMAIL: 1} — counts only
  structured_hits integer not null default 0,          -- regex backstop matches (URL/phone/postcode)
  leaks          integer not null default 0,           -- verify_clean findings; >0 must never egress
  created_at     timestamptz not null default now()
);

create index redaction_events_agent_idx on ops.redaction_events (agent_name, created_at desc);

-- ---------------------------------------------------------------------------
-- LLM egress log (ops) — one row per external provider call, joined to its redaction
-- ---------------------------------------------------------------------------

create table ops.llm_egress_log (
  id            uuid primary key default gen_random_uuid(),
  agent_name    text not null,
  task          text not null,              -- logical task name ('draft_reply', 'classify_intent')
  sensitivity   text not null check (sensitivity in ('sensitive', 'bulk')),
  provider      text not null check (provider in ('bedrock', 'fireworks')),
  model         text not null,
  redaction_ref text not null references ops.redaction_events (ref),
  prompt_chars  integer not null default 0,
  status        text not null default 'sent' check (status in ('sent', 'refused', 'failed')),
  detail        text,
  created_at    timestamptz not null default now()
);

create index llm_egress_log_agent_idx on ops.llm_egress_log (agent_name, created_at desc);

-- ---------------------------------------------------------------------------
-- Per-user style corpus (A9 §3) — the user's own sent mail, teaching their voice
-- ---------------------------------------------------------------------------

create table app.draft_style_examples (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references app.os_users (id) on delete cascade,
  subject    text,
  body_text  text not null,
  sent_at    timestamptz,
  created_at timestamptz not null default now()
);

create index draft_style_examples_user_idx on app.draft_style_examples (user_id, sent_at desc);

-- ---------------------------------------------------------------------------
-- A9 review queue — drafts never send themselves (Mail.Send is not in the agent's allow-list)
-- ---------------------------------------------------------------------------

create type app.email_draft_status as enum (
  'pending_review',  -- written by A9, awaiting the owning user
  'approved',        -- user accepted; sending is a separate human action (WP 4.5)
  'rejected',        -- user declined
  'discarded'        -- pipeline self-rejected (validator caught credential/injection-shaped output)
);

create table app.email_drafts (
  id             uuid primary key default gen_random_uuid(),
  -- The user whose mailbox and voice this draft belongs to. Per-user isolation runs off this.
  author_user_id uuid not null references app.os_users (id) on delete cascade,
  in_reply_to    uuid references app.emails (id) on delete set null,
  matter_id      uuid references app.matters (id) on delete set null,
  subject        text,
  body_text      text not null,
  status         app.email_draft_status not null default 'pending_review',
  provider       text,
  model          text,
  redaction_ref  text references ops.redaction_events (ref),
  -- Validator findings when the pipeline discarded its own output; null on a clean draft.
  discard_reasons text[],
  created_at     timestamptz not null default now(),
  decided_at     timestamptz,
  decided_by     uuid references app.os_users (id)
);

create index email_drafts_author_idx on app.email_drafts (author_user_id, created_at desc);
create index email_drafts_matter_idx on app.email_drafts (matter_id);

-- ---------------------------------------------------------------------------
-- RLS — D39 per-user privacy. A drafter is a private assistant, not a firm-visible queue.
-- ---------------------------------------------------------------------------

alter table app.draft_style_examples enable row level security;
alter table app.draft_style_examples force row level security;
alter table app.email_drafts         enable row level security;
alter table app.email_drafts         force row level security;

-- Own-record rule (D43): the corpus is the user's own sent mail — nobody else reads it, and
-- there is no admin override, because an override would make every mailbox readable by proxy.
create policy draft_style_examples_own on app.draft_style_examples
  for all using (user_id = app.jwt_uid()) with check (user_id = app.jwt_uid());

-- Drafts likewise: authored for you, from your mailbox, in your voice.
create policy email_drafts_own on app.email_drafts
  for all using (author_user_id = app.jwt_uid())
  with check (author_user_id = app.jwt_uid());

grant select, insert, update, delete on app.draft_style_examples to authenticated;
grant select, insert, update, delete on app.email_drafts to authenticated;
-- Ops audit is firm-general: staff can see that redaction ran and what egressed, never the values.
grant select on ops.redaction_events to authenticated;
grant select on ops.llm_egress_log  to authenticated;

-- ---------------------------------------------------------------------------
-- Register the agent (A9) with the orchestrator (WP 6.1)
-- ---------------------------------------------------------------------------
--
-- Note what is absent: 'email.send'. A9 drafts and stops. The never-send guarantee is enforced
-- here (the agent cannot propose a send action at all) as well as in code — two independent
-- controls, because "the drafter mailed a client by itself" is the one failure with no undo.

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'a9-drafter',
  'A9 — per-user email reply drafting into a private review queue (never sends)',
  array['draft.create'],
  array['llm/bedrock-credentials', 'llm/fireworks-api-key']
) on conflict (name) do nothing;
