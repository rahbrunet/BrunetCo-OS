"""FamilyRecordStore — the repository abstraction between the application and wherever family
data lives (spec §3.1 / D3).

v1: `PostgresFamilyRecordStore` materializes the canonical record from the app.* tables through
a caller-supplied RLS-scoped connection (D44 — the store never owns credentials and never sees
the service-role key on user paths). The future blockchain fork implements this same protocol
against a permissioned chain without redesigning callers (tracker F.1).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import psycopg

from py_shared.domain.records import CountryRecord, FamilyRecord, FamilyRecordExport


class FamilyRecordStore(Protocol):
    def get(self, family_id: UUID) -> FamilyRecord | None: ...


class PostgresFamilyRecordStore:
    """Materializes FamilyRecord from Postgres. RLS applies via the supplied connection."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self, family_id: UUID) -> FamilyRecord | None:
        fam = self._conn.execute(
            """
            select f.id, f.reference, f.title, f.family_type::text, c.code, c.name
              from app.families f join app.clients c on c.id = f.client_id
             where f.id = %s
            """,
            (family_id,),
        ).fetchone()
        if fam is None:
            return None

        rows = self._conn.execute(
            """
            select m.id, m.reference, m.jurisdiction_code, m.jurisdiction_segment,
                   m.application_no, m.registration_no, m.filing_date, m.registration_date,
                   m.status::text, m.relationship_type::text, m.parent_matter_id,
                   u.display_name, a.full_name
              from app.matters m
              left join app.os_users u on u.id = m.responsible_user_id
              left join app.contacts a on a.id = m.responsible_associate_id
             where m.family_id = %s
             order by m.jurisdiction_code, m.jurisdiction_segment
            """,
            (family_id,),
        ).fetchall()

        countries = [
            CountryRecord(
                matter_id=r[0], reference=r[1], country=r[2], segment=r[3],
                application_no=r[4], registration_no=r[5], filing_date=r[6],
                registration_date=r[7], status=r[8], relationship_type=r[9],
                parent_matter_id=r[10], responsible_attorney=r[11], responsible_associate=r[12],
            )
            for r in rows
        ]
        return FamilyRecord(
            family_id=fam[0], reference=fam[1], title=fam[2], family_type=fam[3],
            client_code=fam[4], applicant=fam[5], countries=countries,
            generated_at=datetime.now(UTC),
        )


def export_family_record(record: FamilyRecord, signing_key: str, key_id: str) -> FamilyRecordExport:
    """Produce a signed FamilyRecordExport.

    v1 interim: HMAC-SHA256 with a Bitwarden-held key over the canonical (sorted-key) JSON.
    TODO(F.1/portal): replace with asymmetric signing so recipients can verify without the key.
    """
    canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    signature = hmac.new(signing_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return FamilyRecordExport(record=record, signature=signature, key_id=key_id)
