// Entra ID (MSAL) auth for the SPA — auth-code + PKCE (D44).
// The SPA acquires an Entra *access token* for the API scope and sends it as a Bearer token;
// the API validates it and exchanges it per-request for a Supabase JWT. The SPA never sees the
// Supabase JWT and never touches the service-role key.
//
// Dev mode: with no real tenant configured, `getAccessToken` returns a `dev:<uuid>:<email>`
// token that the API accepts when AUTH_DEV_MODE=1. Swap in real MSAL for a live tenant.

import {
  PublicClientApplication,
  type Configuration,
  InteractionRequiredAuthError,
} from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID;
const clientId = import.meta.env.VITE_ENTRA_SPA_CLIENT_ID;
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE;

const PLACEHOLDER = "00000000-0000-0000-0000-000000000000";
const isConfigured = tenantId !== PLACEHOLDER && clientId !== PLACEHOLDER;

const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
  },
  cache: { cacheLocation: "sessionStorage" },
};

const pca = isConfigured ? new PublicClientApplication(msalConfig) : null;

export async function getAccessToken(): Promise<string | null> {
  if (!pca) {
    // Dev fallback — a stable mock identity the API trusts only when AUTH_DEV_MODE=1.
    return "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com";
  }
  await pca.initialize();
  const account = pca.getAllAccounts()[0];
  const request = { scopes: [apiScope], account };
  try {
    const result = account
      ? await pca.acquireTokenSilent(request)
      : await pca.acquireTokenPopup(request);
    return result.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      const result = await pca.acquireTokenPopup(request);
      return result.accessToken;
    }
    throw err;
  }
}
