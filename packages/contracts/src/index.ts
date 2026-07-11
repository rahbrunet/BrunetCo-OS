// Thin typed fetch wrapper over the generated OpenAPI types.
// The generated file (src/generated/schema.ts) is a build artifact — run
// `python make.py gen-contracts` to produce it. Until then, the fallback type below keeps the
// package type-checkable so the workspace builds before the first generation.

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - generated at build time by `gen-contracts`; absent on a fresh clone.
export type * as Schema from "./generated/schema";

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface ApiClientOptions {
  baseUrl: string;
  /** Entra access token; the API exchanges it for a Supabase JWT per request (D44). */
  getAccessToken?: () => Promise<string | null>;
}

export function createApiClient(opts: ApiClientOptions) {
  async function request<T>(path: string): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = await opts.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    const resp = await fetch(`${opts.baseUrl}${path}`, { headers });
    if (!resp.ok) throw new Error(`API ${path} failed: ${resp.status}`);
    return (await resp.json()) as T;
  }

  return {
    health: () => request<HealthResponse>("/api/v1/health"),
  };
}
