// App-wide API client, wired to the typed contracts package and MSAL auth.
import { createApiClient } from "@brunetco/contracts";

import { getAccessToken } from "../auth/msal";

export const api = createApiClient({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  getAccessToken,
});
