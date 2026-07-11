# Entra ID App Registrations (WP 0.7 — placeholders)

Two registrations: the SPA (public client) and the API (protected resource). Fill the IDs into
`.env.local` / Bitwarden. **These are placeholders — replace with the dev-tenant values.**

## API app registration (`brunetco-os-api`)

- **Application ID URI:** `api://brunetco-os`
- **Exposed scope:** `access_as_user` → full URI `api://brunetco-os/access_as_user`
- **Accepted token version:** 2
- Record: `ENTRA_TENANT_ID`, `ENTRA_API_CLIENT_ID`, `ENTRA_API_AUDIENCE=api://brunetco-os`

## SPA app registration (`brunetco-os-web`)

- **Platform:** Single-page application (auth-code + PKCE)
- **Redirect URIs:** `http://127.0.0.1:5173`, `http://localhost:5173` (add prod origins later)
- **API permission:** delegated `api://brunetco-os/access_as_user` (grant admin consent)
- Record: `VITE_ENTRA_TENANT_ID`, `VITE_ENTRA_SPA_CLIENT_ID`, `VITE_ENTRA_API_SCOPE`

## Local-dev mock

With `AUTH_DEV_MODE=1` the API accepts a mock token `dev:<uuid>:<email>` and the SPA sends a fixed
dev identity (`src/auth/msal.ts`). This lets the whole stack run before a tenant exists. **Never
enable `AUTH_DEV_MODE` in production.** Production Entra token validation (JWKS, aud/iss/exp) lands
in WP 0.8 alongside the user table — see `py_shared/auth.py:validate_entra_token`.
