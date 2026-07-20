-- Migration 0022 — register the orchestrator project planner (WP 5.6, spec §M9).
--
-- Unscripted projects (launch_adhoc_project) need no new tables — they reuse app.projects with a
-- null template. The only schema change is registering the planner agent so its LLM egress is
-- attributed in ops.redaction_events / ops.llm_egress_log alongside every other external-LLM
-- caller (D45). It proposes no actions and holds no secret slots: it drafts a plan a human then
-- edits and launches.

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'a0-planner',
  'Orchestrator — drafts an editable project plan from a natural-language description (WP 5.6)',
  array[]::text[],
  array[]::text[]
) on conflict (name) do nothing;
