"""Matter CRUD endpoints (WP 1.1).

References follow Appendix A / M1-R1: the jurisdiction segment is an ordered per-family
sequence (``US`` → ``US2`` → ``US3`` …); ``PCT`` / ``MP`` are sibling matters, and the
relationship to a parent (continuation, CIP, PCT national phase, Madrid designation, …) lives
in ``parent_matter_id`` + ``relationship_type`` — never in the reference string.

RLS-scoped (D44): matter visibility follows the family (``app.can_see_matter`` →
``app.can_see_family``). The routes never check authorization; Postgres decides.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException
from py_shared.domain import generate_matter_reference
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/matters", tags=["matters"])

RelationshipType = Literal[
    "continuation",
    "cip",
    "divisional",
    "pct_national_phase",
    "madrid_designation",
    "tm_extension",
    "external_priority",
    "other",
]
MatterStatus = Literal[
    "pending", "filed", "published", "allowed", "issued", "registered",
    "abandoned", "client_abandoned", "expired", "closed",
]

_COLUMNS = (
    "id, family_id, reference, jurisdiction_code, jurisdiction_segment, parent_matter_id,"
    " relationship_type::text, status::text, application_no, registration_no, filing_date,"
    " registration_date, small_entity, responsible_user_id, responsible_associate_id, created_at"
)


class MatterCreate(BaseModel):
    family_id: UUID
    jurisdiction_code: str
    # Bare jurisdiction/vehicle base for sequencing: US, USP, CA, EP, PCT, MP … Empty string
    # for a no-jurisdiction advisory matter. The numbered segment (US2, US3) is generated.
    segment_base: str
    parent_matter_id: UUID | None = None
    relationship_type: RelationshipType | None = None
    status: MatterStatus = "pending"
    application_no: str | None = None
    registration_no: str | None = None
    filing_date: date | None = None
    registration_date: date | None = None
    small_entity: bool | None = None  # null → inherit client default (M3-R6)
    responsible_user_id: UUID | None = None
    responsible_associate_id: UUID | None = None


class MatterUpdate(BaseModel):
    # Identity fields (reference, jurisdiction, segment) are immutable — not updatable here.
    parent_matter_id: UUID | None = None
    relationship_type: RelationshipType | None = None
    status: MatterStatus | None = None
    application_no: str | None = None
    registration_no: str | None = None
    filing_date: date | None = None
    registration_date: date | None = None
    small_entity: bool | None = None
    responsible_user_id: UUID | None = None
    responsible_associate_id: UUID | None = None


class MatterOut(BaseModel):
    id: UUID
    family_id: UUID
    reference: str
    jurisdiction_code: str
    jurisdiction_segment: str
    parent_matter_id: UUID | None
    relationship_type: RelationshipType | None
    status: MatterStatus
    application_no: str | None
    registration_no: str | None
    filing_date: date | None
    registration_date: date | None
    small_entity: bool | None
    responsible_user_id: UUID | None
    responsible_associate_id: UUID | None
    created_at: datetime


def _row_to_matter(row: tuple[Any, ...]) -> MatterOut:
    return MatterOut(
        id=row[0], family_id=row[1], reference=row[2], jurisdiction_code=row[3],
        jurisdiction_segment=row[4], parent_matter_id=row[5], relationship_type=row[6],
        status=row[7], application_no=row[8], registration_no=row[9], filing_date=row[10],
        registration_date=row[11], small_entity=row[12], responsible_user_id=row[13],
        responsible_associate_id=row[14], created_at=row[15],
    )


@router.post("", response_model=MatterOut, status_code=201)
def create_matter(body: MatterCreate, identity: Identity) -> MatterOut:
    # DB also enforces this (check constraint), but a 422 is clearer than a 400 for the caller.
    if body.parent_matter_id is not None and body.relationship_type is None:
        raise HTTPException(
            status_code=422, detail="relationship_type is required when parent_matter_id is set"
        )
    # Insert without `returning` then read back — see create_family: `insert ... returning`
    # also enforces the SELECT policy, and can_see_matter() cannot see the in-flight row.
    matter_id = uuid4()
    try:
        with identity.connection() as conn:
            segment, reference = generate_matter_reference(
                conn, body.family_id, body.segment_base
            )
            conn.execute(
                """
                insert into app.matters
                  (id, family_id, reference, jurisdiction_code, jurisdiction_segment,
                   parent_matter_id, relationship_type, status, application_no, registration_no,
                   filing_date, registration_date, small_entity, responsible_user_id,
                   responsible_associate_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (matter_id, body.family_id, reference, body.jurisdiction_code, segment,
                 body.parent_matter_id, body.relationship_type, body.status,
                 body.application_no, body.registration_no, body.filing_date,
                 body.registration_date, body.small_entity, body.responsible_user_id,
                 body.responsible_associate_id),
            )
            row = conn.execute(
                f"select {_COLUMNS} from app.matters where id = %s", (matter_id,)
            ).fetchone()
    except LookupError as exc:
        raise HTTPException(status_code=400, detail="Family not found or not visible") from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    assert row is not None  # just inserted under this identity → visible to the follow-up select
    return _row_to_matter(row)


@router.get("/{matter_id}", response_model=MatterOut)
def get_matter(matter_id: UUID, identity: Identity) -> MatterOut:
    with identity.connection() as conn:
        row = conn.execute(
            f"select {_COLUMNS} from app.matters where id = %s", (matter_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return _row_to_matter(row)


@router.patch("/{matter_id}", response_model=MatterOut)
def update_matter(matter_id: UUID, body: MatterUpdate, identity: Identity) -> MatterOut:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    assignments = ", ".join(f"{name} = %s" for name in fields)
    values: list[Any] = list(fields.values())
    values.append(matter_id)
    try:
        with identity.connection() as conn:
            row = conn.execute(
                f"update app.matters set {assignments} where id = %s returning {_COLUMNS}",
                values,
            ).fetchone()
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return _row_to_matter(row)
