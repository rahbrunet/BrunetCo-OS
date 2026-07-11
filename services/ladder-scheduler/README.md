# Service: ladder-scheduler

**Purpose.** Time-based ladder engine — offsets, templates, counts, halt conditions, escalation.
Drives reminders, awaiting-client follow-ups, and dunning. "Silence never abandons": escalate on
exhaustion, never drop.

**Consumers.** A18 reminder/follow-up (WP 6.12), A18 dunning + stop-work (WP 6.13).

**Interface sketch (implemented WP 6.12/6.13, Track B).**
- `schedule(entity, ladder_def)` — per task type + jurisdiction offsets/templates/counts.
- `on_reply(thread)` — halts the ladder (A8 reply detection).
- Review-first sending via the audit queue; dormant `auto_remind` flag (per client/task type,
  default off — earns automation after a review period).
- Dunning: Xero-driven statement ladders from accounting@; auto-clear on payment webhook.

**WP 0.7 status:** skeleton README only. No implementation.
