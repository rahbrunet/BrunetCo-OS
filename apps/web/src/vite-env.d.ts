/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_ENTRA_TENANT_ID: string;
  readonly VITE_ENTRA_SPA_CLIENT_ID: string;
  readonly VITE_ENTRA_API_SCOPE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
