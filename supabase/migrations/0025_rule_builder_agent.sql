-- Migration 0025 — register the Task-Rule Builder agent (WP 6.6, spec §A2).
--
-- A2 needs no tables: it drafts a rule definition a human then saves through the WP 1.3 editor,
-- and its test cases and dry run are computed, never stored. The only schema change is
-- registering the agent so its LLM egress is attributed in ops.redaction_events /
-- ops.llm_egress_log alongside every other external-LLM caller (D45).
--
-- It holds no allowed_actions deliberately: A2 proposes rules, it never installs one. The empty
-- allow-list is the registry-level statement of that.

insert into ops.agents (name, purpose, allowed_actions, allowed_secret_slots)
values (
  'a2-rule-builder',
  'A2 — drafts docket rules from natural language; proposes only, never installs (WP 6.6)',
  array[]::text[],
  array[]::text[]
) on conflict (name) do nothing;
