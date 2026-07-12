"""FastAPI auth dependencies — the D44 bridge, wired per-request.

Every user-facing route depends on `RequestIdentity`: Entra bearer token validated, Supabase
JWT minted, and DB access only through `identity.connection()` (RLS-scoped). No route ever
touches the service-role key.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Request
from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection, validate_entra_token


@dataclass
class RequestIdentity:
    entra: EntraIdentity
    supabase_jwt: str

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """RLS-scoped Postgres connection for this caller (D44)."""
        with user_connection(self.supabase_jwt) as conn:
            yield conn


def get_identity(request: Request) -> RequestIdentity:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.removeprefix("Bearer ").strip()
    try:
        entra = validate_entra_token(token)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return RequestIdentity(entra=entra, supabase_jwt=mint_supabase_jwt(entra))


Identity = Annotated[RequestIdentity, Depends(get_identity)]
