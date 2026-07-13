"""WP 1.8 annuity / maintenance-fee docketing (M1-R8) — pure date math + live-Postgres API."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.domain.annuities import _anniversary, _schedule_years, _year_label

ADMIN = "dev:11111111-1111-1111-1111-111111111111:dev.user@brunetco.com"
STAFF = "dev:22222222-2222-2222-2222-222222222222:dev.agent@brunetco.com"


# --- pure date math ----------------------------------------------------------

def test_schedule_years_range_and_explicit() -> None:
    rng = _schedule_years(Decimal(2), Decimal(20), Decimal(1), None)
    assert rng[0] == Decimal(2) and rng[-1] == Decimal(20) and len(rng) == 19
    us = [Decimal("3.5"), Decimal("7.5"), Decimal("11.5")]
    assert _schedule_years(None, None, Decimal(1), us) == us


def test_anniversary_whole_and_fractional() -> None:
    assert _anniversary(date(2020, 6, 15), Decimal(2), "anniversary") == date(2022, 6, 15)
    # 3.5 years = 3 years 6 months.
    assert _anniversary(date(2020, 1, 15), Decimal("3.5"), "anniversary") == date(2023, 7, 15)


def test_month_end_anniversary() -> None:
    # EPO: year 3 from 2020-03-15 → March 2023, snapped to month end.
    assert _anniversary(date(2020, 3, 15), Decimal(3), "month_end_anniversary") == date(2023, 3, 31)


def test_year_label() -> None:
    assert _year_label(Decimal(5)) == "5"
    assert _year_label(Decimal("3.5")) == "3.5"


# --- API against live Postgres -----------------------------------------------

def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            return conn.execute(
                "select count(*) from app.annuity_schedules"
            ).fetchone()[0] >= 3
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP1.8 migration (0010) not applied")

client = TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _mk_matter(
    su: psycopg.Connection, fam: str, ref: str, juris: str, seg: str,
    filing: str | None = None, reg: str | None = None,
) -> str:
    return str(su.execute(
        "insert into app.matters"
        " (family_id, reference, jurisdiction_code, jurisdiction_segment, filing_date,"
        " registration_date) values (%s, %s, %s, %s, %s, %s) returning id",
        (fam, ref, juris, seg, filing, reg),
    ).fetchone()[0])


class Ctx:
    matter_ca: str
    matter_us: str
    matter_pending: str      # CA, no filing_date
    restricted_matter: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    suffix = uuid.uuid4().hex[:6]
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Annuity Co') returning id",
            (f"A{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        fam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type) "
            "values (%s, '0001', %s, 'Widget', 'patent') returning id",
            (client_id, f"A-{suffix}"),
        ).fetchone()[0]
        c.matter_ca = _mk_matter(su, fam, f"A-{suffix}-CA", "CA", "CA", filing="2020-06-15")
        c.matter_us = _mk_matter(
            su, fam, f"A-{suffix}-US", "US", "US", filing="2019-01-10", reg="2022-01-10"
        )
        c.matter_pending = _mk_matter(su, fam, f"A-{suffix}-CA2", "CA", "CA2")
        rfam = su.execute(
            "insert into app.families (client_id, family_seq, reference, title, family_type,"
            " restricted) values (%s, '0002', %s, 'Secret', 'patent', true) returning id",
            (client_id, f"AR-{suffix}"),
        ).fetchone()[0]
        c.restricted_matter = _mk_matter(
            su, rfam, f"AR-{suffix}-CA", "CA", "CA", filing="2020-06-15"
        )
    yield c


def _gen(token: str, matter_id: str):
    return client.post(
        "/api/v1/docket/annuities", json={"matter_id": matter_id}, headers=_hdr(token)
    )


def test_ca_series_generated_from_filing(ctx: Ctx) -> None:
    resp = _gen(STAFF, ctx.matter_ca)
    assert resp.status_code == 201, resp.text
    tasks = resp.json()
    assert len(tasks) == 19          # years 2..20
    first = tasks[0]
    assert first["annuity_seq"] == 1
    assert first["year_label"] == "2"
    # 2020-06-15 + 2y = 2022-06-15 (Wed, no roll).
    assert first["respond_by"] == "2022-06-15"
    # +6 months grace = 2022-12-15.
    assert first["final_due_date"] == "2022-12-15"


def test_ca_series_is_idempotent(ctx: Ctx) -> None:
    _gen(STAFF, ctx.matter_ca)                 # ensure present
    again = _gen(STAFF, ctx.matter_ca)
    assert again.status_code == 201
    assert again.json() == []                  # nothing new the second time


def test_us_series_from_grant_fractional_years(ctx: Ctx) -> None:
    tasks = _gen(STAFF, ctx.matter_us).json()
    assert [t["year_label"] for t in tasks] == ["3.5", "7.5", "11.5"]
    # grant 2022-01-10 + 3.5y = 2025-07-10.
    assert tasks[0]["respond_by"] == "2025-07-10"


def test_pending_matter_without_base_date_is_422(ctx: Ctx) -> None:
    resp = _gen(STAFF, ctx.matter_pending)
    assert resp.status_code == 422
    assert "filing" in resp.json()["detail"]


def test_no_schedule_jurisdiction_is_422(ctx: Ctx) -> None:
    # Build a matter in a jurisdiction with no annuity schedule (e.g. AU).
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        fam = str(su.execute(
            "select family_id from app.matters where id = %s", (ctx.matter_ca,)
        ).fetchone()[0])
        au = _mk_matter(su, fam, f"AU-{uuid.uuid4().hex[:6]}", "AU", "AU", filing="2020-06-15")
    resp = _gen(STAFF, au)
    assert resp.status_code == 422
    assert "schedule" in resp.json()["detail"]


def test_restricted_matter_hidden_from_staff(ctx: Ctx) -> None:
    assert _gen(STAFF, ctx.restricted_matter).status_code == 404


def test_provenance_written_for_annuity_tasks(ctx: Ctx) -> None:
    _gen(STAFF, ctx.matter_ca)
    prov = client.get(
        f"/api/v1/docket/provenance?matter_id={ctx.matter_ca}", headers=_hdr(STAFF)
    ).json()
    annuity = [p for p in prov if (p.get("trigger_id") or "").startswith("annuity:CA:")]
    assert len(annuity) == 19
    rec = next(p for p in annuity if p["trigger_id"] == "annuity:CA:2")
    assert rec["input_dates"]["base_date"] == "2020-06-15"
    assert rec["calculated_dates"]["respond_by"]["rolled"] == "2022-06-15"
