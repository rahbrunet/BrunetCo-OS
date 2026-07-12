"""Family endpoints (WP 0.8 record surface + WP 1.1 CRUD).

RLS-scoped throughout (D44): every statement runs on the caller's connection, so a family the
caller cannot see returns 404 (SELECT finds no row) and a write they may not make surfaces as a
Postgres error mapped to 403/409 — the routes never check authorization themselves.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException
from py_shared.config import settings
from py_shared.domain import FamilyRecord, PostgresFamilyRecordStore, generate_family_reference
from py_shared.domain.records import FamilyRecordExport
from py_shared.domain.references import family_display_reference
from py_shared.domain.store import export_family_record
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/families", tags=["families"])

FamilyType = Literal["patent", "trademark", "design", "advisory"]

_COLUMNS = (
    "id, client_id, family_seq, reference, title, family_type::text, tm_design, restricted,"
    " created_at"
)


class FamilyCreate(BaseModel):
    client_id: UUID
    title: str
    family_type: FamilyType
    tm_design: bool = False
    restricted: bool = False
    family_seq: str | None = None  # auto-allocated (next 4-digit) when omitted


class FamilyUpdate(BaseModel):
    title: str | None = None
    family_type: FamilyType | None = None
    tm_design: bool | None = None
    restricted: bool | None = None


class FamilyOut(BaseModel):
    id: UUID
    client_id: UUID
    family_seq: str
    reference: str
    display_reference: str  # reference + TM/Design tag (Appendix A) — display only
    title: str
    family_type: FamilyType
    tm_design: bool
    restricted: bool
    created_at: datetime


def _row_to_family(row: tuple[Any, ...]) -> FamilyOut:
    return FamilyOut(
        id=row[0],
        client_id=row[1],
        family_seq=row[2],
        reference=row[3],
        display_reference=family_display_reference(row[3], row[5], row[6]),
        title=row[4],
        family_type=row[5],
        tm_design=row[6],
        restricted=row[7],
        created_at=row[8],
    )


@router.post("", response_model=FamilyOut, status_code=201)
def create_family(body: FamilyCreate, identity: Identity) -> FamilyOut:
    # Insert WITHOUT `returning`, then read the row back: `insert ... returning` also enforces the
    # SELECT policy, and `can_see_family()` (a SECURITY DEFINER re-query of app.families) cannot
    # see the in-flight row mid-INSERT — a separate follow-up SELECT in the same tx can. The id is
    # generated here so the read-back is unambiguous.
    family_id = uuid4()
    try:
        with identity.connection() as conn:
            seq, reference = generate_family_reference(conn, body.client_id, body.family_seq)
            conn.execute(
                """
                insert into app.families
                  (id, client_id, family_seq, reference, title, family_type, tm_design, restricted)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (family_id, body.client_id, seq, reference, body.title, body.family_type,
                 body.tm_design, body.restricted),
            )
            row = conn.execute(
                f"select {_COLUMNS} from app.families where id = %s", (family_id,)
            ).fetchone()
    except LookupError as exc:
        raise HTTPException(status_code=400, detail="Client not found or not visible") from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    assert row is not None  # just inserted under this identity → visible to the follow-up select
    return _row_to_family(row)


@router.get("/{family_id}", response_model=FamilyOut)
def get_family(family_id: UUID, identity: Identity) -> FamilyOut:
    with identity.connection() as conn:
        row = conn.execute(
            f"select {_COLUMNS} from app.families where id = %s", (family_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Family not found")
    return _row_to_family(row)


@router.patch("/{family_id}", response_model=FamilyOut)
def update_family(family_id: UUID, body: FamilyUpdate, identity: Identity) -> FamilyOut:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    assignments = ", ".join(f"{name} = %s" for name in fields)
    values = list(fields.values())
    values.append(family_id)
    try:
        with identity.connection() as conn:
            row = conn.execute(
                f"update app.families set {assignments} where id = %s returning {_COLUMNS}",
                values,
            ).fetchone()
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Family not found")
    return _row_to_family(row)


# --- WP 0.8 Family Record surface (unchanged) ------------------------------


@router.get("/{family_id}/record", response_model=FamilyRecord)
def get_family_record(family_id: UUID, identity: Identity) -> FamilyRecord:
    with identity.connection() as conn:
        record = PostgresFamilyRecordStore(conn).get(family_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Family not found")
    return record


@router.get("/{family_id}/export", response_model=FamilyRecordExport)
def get_family_export(family_id: UUID, identity: Identity) -> FamilyRecordExport:
    with identity.connection() as conn:
        record = PostgresFamilyRecordStore(conn).get(family_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Family not found")
    # Interim signing key (see store.export_family_record TODO): dedicated Bitwarden-held key.
    return export_family_record(record, signing_key=settings.supabase_jwt_secret, key_id="v1-hmac")
