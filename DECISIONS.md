# Repo Tech Decisions Log

Architecture rulings from the spec are **not open** (marked `[ruling]`). Tooling specifics are
recommendations — the team may substitute an equivalent with a one-line rationale here.

Seeded from `WP0.7-scaffold-prompt.md` (Spec v0.17 / Tracker v18).

---

## Architecture rulings (not open)

- **[ruling D44] Auth = user-JWT pass-through to Postgres.** RLS is the real access control,
  not defense-in-depth. The API must **never** use the Supabase service-role key on user-facing
  request paths. FastAPI validates the Entra access token, then exchanges it per-request for a
  Supabase-compatible JWT signed with the Supabase JWT secret. See `packages/py-shared/py_shared/auth.py`.
- **[ruling D43] Every table ships its RLS policy in the same migration.** No "temporarily open"
  tables. Five protected permission domains (time entry / expense entry / invoicing / accounting
  reporting / compensation admin) are separate from firm-general access. (Grant tables are WP 0.8.)
- **[ruling D41] Document identity inversion.** The Postgres record is a document's canonical
  identity; SharePoint driveItem ID is the storage pointer; folder path is a projection. (Schema is WP 0.8.)
- **[ruling D38] ~6 platform services, not 19 agents.** The A0–A18 roster is a spec taxonomy.
  Buildable architecture is the `services/*` packages here; each "agent" is later config + prompts.
- **[ruling — design review §4/§7.5] MCP server surface from day one**, derived from the same
  OpenAPI schema so it never drifts.
- **[ruling — spec §2] Browser-native.** Every workflow works in a plain browser. No Electron,
  no required desktop installs.

## Service-role key usage register (D44)

The service-role key is permitted **only** for the enumerated uses below. Each new use must be
added here in the same PR that introduces it.

| Use | Where | Justification |
|---|---|---|
| DB migrations | `supabase/` CLI | Schema changes run as owner |
| _(none yet on request paths)_ | — | User paths use JWT pass-through only |

## Tooling choices (team-vetoable)

| Area | Choice | Rationale / alternative |
|---|---|---|
| Python pkg mgr | `uv` workspaces | Fast; Poetry acceptable substitute |
| JS pkg mgr | `pnpm` workspaces | Efficient; npm/yarn acceptable |
| Contract gen | `openapi-typescript` + typed fetch wrapper | Orval acceptable |
| Task runner | `make.py` (+ mirror `Makefile`) | Windows has no `make`; Python runner is portable |
| Event queue | Postgres `events` table + `FOR UPDATE SKIP LOCKED` poller | No external broker; pgmq acceptable |
| CI | GitHub Actions | Substitute freely |
| Secrets | Bitwarden Secrets Manager (`bws`) | D10 ruling; loader in `py-shared/secrets.py` |

## Reuse notes — existing firm code (WP 6.9 / 6.10 gating code, found on-machine)

Located `2026-07-11` under `C:\Users\robja`. These unblock the previously-⛔ agent work packages
without a separate handover:

- **`email-assistant/src/`** — RAG email drafter (`draft.py`, `embed.py`, `ingest.py`, `thread.py`,
  `redactor.py`, `corpus_updater.py`, `outlook_draft.py`, `response_validator.py`; Chroma vector DB).
  → Template for the **`drafting/`** service (A9, WP 6.9). Port the ingest→embed→retrieve→draft→
  validate pipeline; replace Chroma with pgvector per design review §6.3.
- **`email-assistant/quote-tool/Quote-Tool/backend/`** — the live website quote tool.
  → Template for the **`prospecting/`**-adjacent A10 quote agent (WP 6.10). Wire into OS CRM quote
  records post-cutover.
- **`OneDrive/.../Automation/brunet-os/`** — owner's personal automation-agency OS (orchestrator,
  agents, pods, approval `state/` queue, n8n). Different domain; **reference only** (owner ruling
  2026-07-11). Reusable patterns: orchestrator + approval-queue shape for `orchestrator/` (A0).

## WP 0.7 acceptance

- [ ] `make dev` boots Supabase local + API + SPA; health endpoint round-trips typed data
- [ ] Entra login works against a dev tenant registration (or documented mock for local dev)
- [ ] RLS proof pytest passes **against Postgres directly** with two user JWTs
- [ ] MCP server boots and serves the demo tool
- [ ] Worker consumes a demo event
- [ ] No secret material in the repo; Bitwarden loader + local fallback documented
- [ ] CI fully green; contract-drift check fails when the API schema changes without regeneration
- [ ] 8 service package READMEs in place; `DECISIONS.md` seeded

## Out of scope for 0.7 (→ WP 0.8+)

Domain models (Client/Family/Matter/Contact/Task/User), `FamilyRecordStore`, permission grant
tables, work-item substrate schema, module features, integration adapters beyond the auth bridge,
deployment/hosting selection. **Data-residency note (design review §6.5):** scan standard engagement
terms before locking a US region — see `infra/data-residency.md`.

---

## Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-11 | Scaffold created at `C:\Users\robja\brunetco-os`; personal brunet-os = reference only | Owner (James) ruling |
| 2026-07-12 | **WP 1.1** — TM/Design tag is **display-only**, not stored in `families.reference`. `family_display_reference()` appends `(TM)`/`Design`; the stored reference stays `{Client}-{Seq}` (already unique). | Keeps the identity key free of matter-type-ish data (Appendix A / M1-R1); the tag is presentation, like the legacy folder annotations D36 moved into the DB. |
| 2026-07-12 | **WP 1.1** — CRUD create routes insert with an explicit id and **no `RETURNING`**, then read the row back in the same tx. | `insert … returning` also enforces the SELECT policy, and `can_see_family`/`can_see_matter` (SECURITY DEFINER re-query) can't see the in-flight row mid-INSERT → RLS would reject the write. Follow-up SELECT sees it. No schema/policy change; RLS stays the sole control (D44). |
