# Secrets — Bitwarden Secrets Manager (D10)

No secret material lives in the repo or in committed env files. Production secrets come from
**Bitwarden Secrets Manager** via the `bws` CLI; local dev uses `.env.local` (gitignored).

## Loading

`py_shared.secrets.load_secrets_into_env()` runs at process startup. If `BWS_ACCESS_TOKEN` +
`BWS_PROJECT_ID` are present, it pulls every secret in the project into the environment (values are
never logged). Otherwise it falls back to `.env.local`.

## Runtime broker slots

Agent-facing secrets are *not* injected into the environment. They are fetched one at a time
through the credential broker (`py_shared.orchestrator.fetch_secret`), which checks the agent's
`ops.agents.allowed_secret_slots` allow-list first and only then calls the fetcher. In production
that fetcher is `py_shared.secrets.BitwardenSecretFetcher`; with no `BWS_ACCESS_TOKEN` /
`BWS_PROJECT_ID` set it degrades to a `dev-secret::<slot>` placeholder so the authorization path
stays testable without a real credential.

Slot names are the Bitwarden secret keys verbatim, namespaced by agent domain:

| Slot | Agent |
|---|---|
| `cipo/twocaptcha-api-key` | `cipo-watcher` (WP 6.2) |

The project listing is cached per process; a rotated value is picked up on restart (or via
`BitwardenSecretFetcher.refresh()`), and a slot added after startup resolves on first miss.

## Bitwarden project layout

One project **`brunetco-os`** (add `-staging` / `-prod` variants as environments appear). Secret
keys match the env var names in `.env.example`:

| Key | Used by |
|---|---|
| `SUPABASE_DB_URL`, `SUPABASE_JWT_SECRET` | API auth bridge (D44) |
| `SUPABASE_SERVICE_ROLE_KEY` | migrations / system workers / admin ONLY (D44 register) |
| `ENTRA_TENANT_ID`, `ENTRA_API_CLIENT_ID` | Entra token validation |
| _(added as modules land)_ | Xero, Graph, DocuSign, hunter.io, … |

## CI

CI uses repository/organization secrets (not Bitwarden) for the ephemeral Postgres and any build
credentials. No production secrets are needed to run the scaffold's tests.

## Machine-access provisioning

A machine account / service token per environment; least-privilege project access. Rotate on
personnel change. (The AppColl-password-in-plaintext incident is why this pattern is mandatory.)
