"""Audit trail read endpoint (M1-R6, WP 1.5).

The audit_log is written exclusively by database triggers (migration 0008) — there is no write
surface here, by design. Reads are RLS-scoped (D44): an audit row inherits the visibility of the
record it describes (family-scoped rows follow `can_see_family`; family-less rows — rule edits,
permission grants — are permissions-admin reading).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

AuditAction = Literal["insert", "update", "delete"]


class AuditEntry(BaseModel):
    id: UUID
    table_name: str
    row_id: UUID
    family_id: UUID | None
    action: AuditAction
    changed_by: UUID | None  # null = system/migration path
    changed_at: datetime
    old_row: dict[str, Any] | None
    new_row: dict[str, Any] | None
    changed_fields: list[str] | None


@router.get("", response_model=list[AuditEntry])
def audit(
    identity: Identity,
    table_name: str | None = None,
    row_id: UUID | None = None,
    family_id: UUID | None = None,
    changed_from: datetime | None = None,
    changed_to: datetime | None = None,
) -> list[AuditEntry]:
    """Search the audit trail — filterable by table, row, family, and date range."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, table_name, row_id, family_id, action::text, changed_by, changed_at,
                   old_row, new_row, changed_fields
              from app.audit_log
             where (%(table_name)s::text is null or table_name = %(table_name)s)
               and (%(row_id)s::uuid is null or row_id = %(row_id)s)
               and (%(family_id)s::uuid is null or family_id = %(family_id)s)
               and (%(changed_from)s::timestamptz is null or changed_at >= %(changed_from)s)
               and (%(changed_to)s::timestamptz is null or changed_at < %(changed_to)s)
             order by changed_at desc
             limit 1000
            """,
            {"table_name": table_name, "row_id": row_id, "family_id": family_id,
             "changed_from": changed_from, "changed_to": changed_to},
        ).fetchall()
    return [
        AuditEntry(
            id=r[0], table_name=r[1], row_id=r[2], family_id=r[3], action=r[4],
            changed_by=r[5], changed_at=r[6], old_row=r[7], new_row=r[8], changed_fields=r[9],
        )
        for r in rows
    ]
