"""Map Postgres errors to HTTP status codes.

RLS is the access control (D44), so an RLS denial arrives here as a Postgres error, not an
app-layer check. This translates the ones the CRUD routes can provoke into clean HTTP:
insufficient-privilege (RLS) → 403, unique-violation (reference clash) → 409, FK/check → 4xx.
"""
from __future__ import annotations

import psycopg
from fastapi import HTTPException


def map_db_error(exc: psycopg.Error) -> HTTPException:
    message = exc.diag.message_primary or str(exc)
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        # RLS policy denied the write (e.g. non-staff, or a family the caller cannot see).
        return HTTPException(status_code=403, detail=f"Not permitted: {message}")
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return HTTPException(status_code=409, detail=f"Already exists: {message}")
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return HTTPException(status_code=400, detail=f"Referenced record missing: {message}")
    if isinstance(exc, psycopg.errors.CheckViolation):
        return HTTPException(status_code=422, detail=f"Invalid: {message}")
    return HTTPException(status_code=400, detail=message)
