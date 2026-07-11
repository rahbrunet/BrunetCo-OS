"""Typed settings, loaded from environment (which is populated by Bitwarden in prod,
or `.env.local` in dev — see secrets.py)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    # Supabase
    supabase_url: str = Field(default="http://127.0.0.1:54321")
    supabase_db_url: str = Field(default="postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    supabase_jwt_secret: str = Field(
        default="super-secret-jwt-token-with-at-least-32-characters-long"
    )
    # Permitted ONLY for migrations/system workers/admin (D44). Never used on user request paths.
    supabase_service_role_key: str = Field(default="")

    # Entra ID
    entra_tenant_id: str = Field(default="00000000-0000-0000-0000-000000000000")
    entra_api_client_id: str = Field(default="00000000-0000-0000-0000-000000000000")
    entra_api_audience: str = Field(default="api://brunetco-os")
    # Dev-only bypass of Entra validation. MUST be 0 in production.
    auth_dev_mode: bool = Field(default=True)

    # Bitwarden Secrets Manager
    bws_access_token: str = Field(default="")
    bws_project_id: str = Field(default="")


settings = Settings()
