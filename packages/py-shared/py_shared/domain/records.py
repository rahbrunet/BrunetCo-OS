"""Canonical Family Record schema (spec §3.1, D3 blockchain-fork strategy).

Every family's core bibliographic/status data is expressible as this versioned JSON schema:
family ID, title, applicant/owner, and a per-country record for every jurisdiction the family
touches. In v1 it is materialized from Postgres; the same schema is the future chain-write
payload and the FamilyRecordExport body — which is why it lives here and not in the API app.

Operational data (time, billing, email content, EOS) is deliberately NOT part of this record
and never will be.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class CountryRecord(BaseModel):
    """Per-jurisdiction slice of a family: one entry per matter."""

    matter_id: UUID
    reference: str
    country: str = Field(description="Internal jurisdiction code (EP-EPO/EP-EUIPO disambiguated)")
    segment: str = Field(description="Ordered per-family segment: USP, US, US2, PCT, MP, ...")
    application_no: str | None = None
    registration_no: str | None = None
    filing_date: date | None = None
    registration_date: date | None = None
    status: str
    relationship_type: str | None = None
    parent_matter_id: UUID | None = None
    responsible_attorney: str | None = None
    responsible_associate: str | None = None


class FamilyRecord(BaseModel):
    """The canonical, versioned Family Record."""

    schema_version: int = SCHEMA_VERSION
    family_id: UUID
    reference: str
    title: str
    family_type: str
    client_code: str
    applicant: str = Field(description="Client (applicant/owner) name")
    countries: list[CountryRecord] = Field(default_factory=list)
    generated_at: datetime


class FamilyRecordExport(BaseModel):
    """Signed, shareable snapshot (spec §3.1) — the controlled foreign-associate sharing surface
    today, the Client Portal substrate (D34) and chain-write path later."""

    record: FamilyRecord
    signature: str = Field(description="HMAC-SHA256 over the canonical record JSON (v1 interim)")
    key_id: str = Field(description="Identifier of the signing key used")
