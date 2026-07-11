# BrunetCo OS

Custom IP practice-management platform for a Canadian/US patent & trademark agency
(~10 users, scaling to ~30), replacing AppColl.

**This repository is the WP 0.7 scaffold** — repository skeleton only. No domain models
(WP 0.8) and no feature code. Everything here runs and CI is green.

Source of truth for scope: `IP-OS Spec v0.17` + `Tracker v18` (SharePoint design docs).
Architecture rulings are recorded in [`DECISIONS.md`](DECISIONS.md).

---

## Layout

```
apps/
  api/            FastAPI (Python 3.12+) — schema source of truth
  web/            React + TypeScript SPA (Vite)
  workers/        Event-driven worker processes (Postgres-backed queue)
services/         ~6 platform-service skeletons + orchestrator + KB (packages, README-only in 0.7)
  email-pipeline/     A8/A9/A10, M6
  drafting/           A9/A11/A18 templates, M8
  prospecting/        A14–A17
  ladder-scheduler/   A18 deadlines / awaiting-client / dunning
  watchers/           A1/A3 per-office adapters
  browser-automation/ A12/A13
  orchestrator/       A0 — approval queue + egress gate
  knowledge-base/     KB service (citation-aware retrieval)
packages/
  contracts/      Generated TS client + types from the API OpenAPI schema
  py-shared/      Shared Python: config, auth (D44 JWT bridge), Supabase client, Bitwarden loader
supabase/
  migrations/     SQL migrations + RLS policies (every table ships its RLS policy — D43)
infra/
  ci/             CI workflow files
  entra.md        Entra app registration (placeholders)
  secrets.md      Bitwarden Secrets Manager project structure
```

## Prerequisites

- Python 3.12+ (repo pins via `uv`)
- Node 20+ and `pnpm` (`corepack enable pnpm`)
- `uv` (Python package/workspace manager)
- Docker (for the Supabase local stack)
- Supabase CLI (`npx supabase` works if not installed globally)
- `bws` (Bitwarden Secrets Manager CLI) for real secrets; local dev uses `.env.local`

## Quickstart

```bash
cp .env.example .env.local     # fill in dev values (never commit)
python make.py dev             # boots Supabase local + API + web  (or: make dev)
python make.py test            # pytest + vitest + RLS proof + contract-drift
python make.py gen-contracts   # regenerate packages/contracts from the API OpenAPI schema
python make.py lint            # ruff + eslint + typecheck
```

`make.py` is a cross-platform task runner (Windows has no `make` by default). A `Makefile`
mirrors the same targets for Unix shells.

## What "green" means here

See the [acceptance checklist](DECISIONS.md#wp-07-acceptance) — health endpoint round-trips
typed data, RLS proof pytest passes against Postgres directly with two user JWTs, MCP server
boots, a worker consumes a demo event, no secrets in the repo, CI fully green.
