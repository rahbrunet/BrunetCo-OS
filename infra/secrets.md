# Secrets — Bitwarden Secrets Manager (D10)

No secret material lives in the repo or in committed env files. Production secrets come from
**Bitwarden Secrets Manager** via the `bws` CLI; local dev uses `.env.local` (gitignored).

## Loading

`py_shared.secrets.load_secrets_into_env()` runs at process startup. If `BWS_ACCESS_TOKEN` +
`BWS_PROJECT_ID` are present, it pulls every secret in the project into the environment (values are
never logged). Otherwise it falls back to `.env.local`.

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
