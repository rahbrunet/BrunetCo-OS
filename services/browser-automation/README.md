# Service: browser-automation

**Purpose.** Session-managed browser-automation framework for portal agents that must operate
office filing sites lacking submission APIs. Field-level audit log; human submits, never the bot.

**Consumers.** A12 (CIPO filing), A13 (USPTO Patent Center), Clarivate Tier 3 if ever needed.

**Interface sketch (implemented Phase 8.5, Track B).**
- `session(portal)` — Bitwarden creds, login, cookie/session management.
- `populate(form, data)` + `stage()` — fill and stage; every field write is audit-logged.
- `await_human_submit()` — an admin review task gates the actual submission (human-approves-sent).
- Verify at build that CIPO/USPTO still lack filing APIs before committing to browser automation.

**WP 0.7 status:** skeleton README only. No implementation.
