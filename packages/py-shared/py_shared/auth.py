"""D44 auth bridge: Entra ID access token -> Supabase-compatible per-request JWT -> RLS.

Flow (never bypassed on user paths):
  1. SPA authenticates against Entra ID (MSAL, auth-code + PKCE) and calls the API with the
     Entra *access token*.
  2. FastAPI validates that token (`validate_entra_token`).
  3. The API mints a short-lived Supabase-compatible JWT (`mint_supabase_jwt`) signed with the
     Supabase JWT secret: sub = OS user UUID, role = "authenticated", plus a claims block
     reserved for WP 0.8 (family ACLs, D43 permission domains, D39 mailbox identity).
  4. Every DB access opens a *request-scoped* connection that applies those claims so Postgres
     RLS — not application code — is the access control (D44).

The Supabase service-role key is NEVER used here. It is restricted to migrations / system
workers / admin scripts, each enumerated in DECISIONS.md.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import jwt
import psycopg

from py_shared.config import settings


@dataclass
class EntraIdentity:
    """The validated caller. In WP 0.8 this maps to an OS user row + family ACLs."""

    os_user_id: str
    email: str
    # Reserved for WP 0.8 — populated from the OS user/permission tables, not from Entra.
    claims: dict[str, Any] = field(default_factory=dict)


def validate_entra_token(access_token: str) -> EntraIdentity:
    """Validate an Entra access token and resolve it to an OS identity.

    Dev mode (AUTH_DEV_MODE=1) accepts a mock token of the form `dev:<uuid>:<email>` so the
    stack runs without a live tenant. Production validation (signature via the tenant JWKS,
    audience = ENTRA_API_AUDIENCE, issuer = tenant) is implemented at WP 0.8 alongside the
    user table; this scaffold ships the structure and the dev path.
    """
    if settings.auth_dev_mode:
        # `dev:<uuid>:<email>` — mock identity for local dev and CI.
        parts = access_token.split(":", 2)
        if len(parts) == 3 and parts[0] == "dev":
            return EntraIdentity(os_user_id=parts[1], email=parts[2])
        raise ValueError("dev-mode token must look like 'dev:<uuid>:<email>'")

    # TODO(WP 0.8): validate against Entra JWKS, check aud/iss/exp, then look up the OS user.
    raise NotImplementedError("Production Entra validation lands in WP 0.8")


def mint_supabase_jwt(identity: EntraIdentity, ttl_seconds: int = 300) -> str:
    """Mint a short-lived Supabase-compatible JWT signed with the Supabase JWT secret."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": identity.os_user_id,
        "email": identity.email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + ttl_seconds,
        # Reserved claims block — WP 0.8 fills these from the permission domain (D43),
        # family ACLs, and mailbox identity (D39).
        "os_claims": identity.claims,
    }
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


def _claims_from_jwt(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience="authenticated",
    )


@contextmanager
def user_connection(supabase_jwt: str) -> Iterator[psycopg.Connection]:
    """Open a request-scoped Postgres connection that runs under the caller's identity.

    Sets `request.jwt.claims` and the `authenticated` role so RLS policies using `auth.uid()`
    / `current_setting('request.jwt.claims')` evaluate against the real user. This is the ONLY
    supported way to reach the DB on a user path — no service-role connection (D44).
    """
    import json

    claims = _claims_from_jwt(supabase_jwt)
    conn = psycopg.connect(settings.supabase_db_url, autocommit=False)
    try:
        with conn.cursor() as cur:
            # Local-role emulation of Supabase's request context.
            cur.execute("SET ROLE authenticated")
            cur.execute(
                "SELECT set_config('request.jwt.claims', %s, true)",
                (json.dumps(claims),),
            )
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
