"""Staged CSV imports against Postgres (WP 5B.2).

The two-phase shape is the point: stage → a human sees the counts and the rejects → commit.
That gap is what stops a mis-mapped column quietly rewriting three thousand records.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain import csv_io

ADMIN_ID = "11111111-1111-1111-1111-111111111111"

COLUMNS = [
    csv_io.Column("code", "Code", csv_io.TEXT, required=True),
    csv_io.Column("name", "Name", csv_io.TEXT, required=True),
]


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.csv_imports')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP5B.2 migration (0024) not applied")


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


def _cleanup(su: psycopg.Connection, import_id: uuid.UUID) -> None:
    su.execute("delete from app.csv_imports where id = %s", (import_id,))


def test_staging_records_every_row_and_the_counts(su: psycopg.Connection) -> None:
    text = "Code,Name\nA1,Acme\n,Missing code\nB2,Beta\n"
    import_id = csv_io.stage_import(su, "clients", text, COLUMNS, uuid.UUID(ADMIN_ID), "f.csv")
    try:
        counts = su.execute(
            "select status::text, rows_seen, rows_valid, rows_rejected from app.csv_imports "
            " where id = %s",
            (import_id,),
        ).fetchone()
        assert counts[0] == "staged"
        assert counts[1] == 3 and counts[2] == 2 and counts[3] == 1
    finally:
        _cleanup(su, import_id)


def test_staging_writes_nothing_to_the_live_tables(su: psycopg.Connection) -> None:
    """The whole reason for two phases."""
    before = su.execute("select count(*) from app.clients").fetchone()[0]
    import_id = csv_io.stage_import(su, "clients", "Code,Name\nZZ,Zeta\n", COLUMNS,
                                    uuid.UUID(ADMIN_ID))
    try:
        after = su.execute("select count(*) from app.clients").fetchone()[0]
        assert after == before
    finally:
        _cleanup(su, import_id)


def test_a_bad_header_fails_the_whole_run(su: psycopg.Connection) -> None:
    """A file whose columns do not match is the wrong file, not a file with bad rows."""
    import_id = csv_io.stage_import(su, "clients", "Wrong,Header\n1,2\n", COLUMNS,
                                    uuid.UUID(ADMIN_ID))
    try:
        row = su.execute(
            "select status::text, detail from app.csv_imports where id = %s", (import_id,)
        ).fetchone()
        assert row[0] == "failed"
        assert "missing column" in row[1]
        n = su.execute(
            "select count(*) from app.csv_import_rows where import_id = %s", (import_id,)
        ).fetchone()[0]
        assert n == 0
    finally:
        _cleanup(su, import_id)


def test_rejected_rows_keep_their_raw_payload(su: psycopg.Connection) -> None:
    import_id = csv_io.stage_import(su, "clients", "Code,Name\n,Nameless\n", COLUMNS,
                                    uuid.UUID(ADMIN_ID))
    try:
        row = su.execute(
            "select raw, error from app.csv_import_rows where import_id = %s", (import_id,)
        ).fetchone()
        assert row[0] == {"Code": "", "Name": "Nameless"}
        assert "required" in row[1]
    finally:
        _cleanup(su, import_id)


def test_commit_applies_only_valid_rows(su: psycopg.Connection) -> None:
    prefix = uuid.uuid4().hex[:4].upper()
    text = f"Code,Name\n{prefix}1,One\n,Bad\n{prefix}2,Two\n"
    import_id = csv_io.stage_import(su, "clients", text, COLUMNS, uuid.UUID(ADMIN_ID))
    created: list[str] = []

    def committer(conn: psycopg.Connection, row: dict) -> None:
        cid = conn.execute(
            "insert into app.clients (code, name) values (%s, %s) returning id",
            (row["code"], row["name"]),
        ).fetchone()[0]
        created.append(str(cid))

    try:
        result = csv_io.commit_import(su, import_id, committer)
        assert result == {"committed": 2, "failed": 0}
        status = su.execute(
            "select status::text, rows_committed from app.csv_imports where id = %s", (import_id,)
        ).fetchone()
        assert status[0] == "committed" and status[1] == 2
    finally:
        for cid in created:
            su.execute("delete from app.clients where id = %s", (cid,))
        _cleanup(su, import_id)


def test_a_row_whose_commit_fails_is_quarantined_not_fatal(su: psycopg.Connection) -> None:
    """One unexpected constraint violation on row 900 must not discard the 899 good rows."""
    prefix = uuid.uuid4().hex[:4].upper()
    text = f"Code,Name\n{prefix}1,One\n{prefix}2,Boom\n{prefix}3,Three\n"
    import_id = csv_io.stage_import(su, "clients", text, COLUMNS, uuid.UUID(ADMIN_ID))
    created: list[str] = []

    def committer(conn: psycopg.Connection, row: dict) -> None:
        if row["name"] == "Boom":
            raise RuntimeError("simulated failure")
        cid = conn.execute(
            "insert into app.clients (code, name) values (%s, %s) returning id",
            (row["code"], row["name"]),
        ).fetchone()[0]
        created.append(str(cid))

    try:
        result = csv_io.commit_import(su, import_id, committer)
        assert result == {"committed": 2, "failed": 1}
        err = su.execute(
            "select error from app.csv_import_rows "
            " where import_id = %s and error is not null", (import_id,)
        ).fetchone()
        assert "commit failed" in err[0]
    finally:
        for cid in created:
            su.execute("delete from app.clients where id = %s", (cid,))
        _cleanup(su, import_id)


def test_committing_twice_is_refused(su: psycopg.Connection) -> None:
    import_id = csv_io.stage_import(su, "clients", "Code,Name\nQ1,Q\n", COLUMNS,
                                    uuid.UUID(ADMIN_ID))
    try:
        csv_io.commit_import(su, import_id, lambda conn, row: None)
        with pytest.raises(ValueError, match="not staged"):
            csv_io.commit_import(su, import_id, lambda conn, row: None)
    finally:
        _cleanup(su, import_id)


def test_a_failed_header_run_cannot_be_committed(su: psycopg.Connection) -> None:
    import_id = csv_io.stage_import(su, "clients", "Nope\n1\n", COLUMNS, uuid.UUID(ADMIN_ID))
    try:
        with pytest.raises(ValueError, match="not staged"):
            csv_io.commit_import(su, import_id, lambda conn, row: None)
    finally:
        _cleanup(su, import_id)


def test_decimals_survive_staging_without_float_rounding(su: psycopg.Connection) -> None:
    """A Decimal rendered as a float is exactly the precision loss Decimal was chosen to avoid."""
    cols = [csv_io.Column("amount", "Amount", csv_io.DECIMAL)]
    import_id = csv_io.stage_import(su, "fees", "Amount\n1234.56\n", cols, uuid.UUID(ADMIN_ID))
    try:
        parsed = su.execute(
            "select parsed from app.csv_import_rows where import_id = %s", (import_id,)
        ).fetchone()[0]
        assert parsed["amount"] == "1234.56"
    finally:
        _cleanup(su, import_id)
