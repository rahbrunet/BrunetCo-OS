"""Permissions administration (D43) — the API behind the admin screen.

Authorization is NOT checked in this code: every statement runs on the caller's RLS-scoped
connection, so Postgres policies (migration 0003) are the control. A non-admin caller simply
sees no other users' grants and gets an RLS violation (mapped to 403) on writes. That is the
D44 design working as intended — do not add app-layer permission checks that would mask RLS.
"""
from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/admin/permissions", tags=["permissions-admin"])

DOMAINS = [
    "time_entry", "expense_entry", "invoicing", "accounting_reporting", "compensation_admin",
]


class UserGrants(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role_template: str | None
    is_active: bool
    domains: list[str]


class GrantRequest(BaseModel):
    user_id: UUID
    domain: str


class ApplyTemplateRequest(BaseModel):
    user_id: UUID
    template: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    domains: list[str]


def _forbidden(exc: psycopg.Error) -> HTTPException:
    # RLS violations surface as insufficient_privilege / RLS policy errors.
    return HTTPException(status_code=403, detail=f"Not permitted: {exc.diag.message_primary}")


@router.get("", response_model=list[UserGrants])
def list_users(identity: Identity) -> list[UserGrants]:
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select u.id, u.email, u.display_name, u.role_template, u.is_active,
                   coalesce(array_agg(g.domain::text) filter (where g.domain is not null), '{}')
              from app.os_users u
              left join app.permission_grants g on g.user_id = u.id
             group by u.id order by u.display_name
            """
        ).fetchall()
    return [
        UserGrants(user_id=r[0], email=r[1], display_name=r[2], role_template=r[3],
                   is_active=r[4], domains=sorted(r[5]))
        for r in rows
    ]


@router.post("/grants", status_code=201)
def add_grant(body: GrantRequest, identity: Identity) -> dict[str, str]:
    if body.domain not in DOMAINS:
        raise HTTPException(status_code=422, detail=f"Unknown domain {body.domain!r}")
    try:
        with identity.connection() as conn:
            conn.execute(
                """
                insert into app.permission_grants (user_id, domain, granted_by)
                values (%s, %s, %s) on conflict do nothing
                """,
                (body.user_id, body.domain, identity.entra.os_user_id),
            )
    except psycopg.Error as exc:
        raise _forbidden(exc) from exc
    return {"status": "granted"}


@router.delete("/grants/{user_id}/{domain}")
def revoke_grant(user_id: UUID, domain: str, identity: Identity) -> dict[str, str]:
    try:
        with identity.connection() as conn:
            conn.execute(
                "delete from app.permission_grants"
                " where user_id = %s and domain = %s::app.permission_domain",
                (user_id, domain),
            )
    except psycopg.Error as exc:
        raise _forbidden(exc) from exc
    return {"status": "revoked"}


@router.post("/apply-template")
def apply_template(body: ApplyTemplateRequest, identity: Identity) -> dict[str, str]:
    try:
        with identity.connection() as conn:
            row = conn.execute(
                "select domains from app.role_templates where name = %s", (body.template,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"No template {body.template!r}")
            conn.execute("delete from app.permission_grants where user_id = %s", (body.user_id,))
            for domain in row[0]:
                conn.execute(
                    """
                    insert into app.permission_grants (user_id, domain, granted_by)
                    values (%s, %s, %s)
                    """,
                    (body.user_id, domain, identity.entra.os_user_id),
                )
            conn.execute(
                "update app.os_users set role_template = %s where id = %s",
                (body.template, body.user_id),
            )
    except psycopg.Error as exc:
        raise _forbidden(exc) from exc
    return {"status": "applied", "template": body.template}


# Convenience for the SPA: who am I, what can I do.
me_router = APIRouter(prefix="/api/v1", tags=["system"])


@me_router.get("/me", response_model=MeResponse)
def me(identity: Identity) -> MeResponse:
    with identity.connection() as conn:
        rows = conn.execute(
            "select domain::text from app.permission_grants where user_id = %s",
            (identity.entra.os_user_id,),
        ).fetchall()
    return MeResponse(
        user_id=identity.entra.os_user_id,
        email=identity.entra.email,
        domains=sorted(r[0] for r in rows),
    )
