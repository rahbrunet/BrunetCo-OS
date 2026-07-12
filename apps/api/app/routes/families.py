"""Family Record endpoint — the FamilyRecordStore surfaced over the API (WP 0.8).

RLS-scoped: a family the caller cannot see returns 404 (the store's SELECT simply finds no row).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.config import settings
from py_shared.domain import FamilyRecord, PostgresFamilyRecordStore
from py_shared.domain.records import FamilyRecordExport
from py_shared.domain.store import export_family_record

from app.deps import Identity

router = APIRouter(prefix="/api/v1/families", tags=["families"])


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
