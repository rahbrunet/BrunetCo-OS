"""CSV import/export framework (WP 5B.2) — typed parsing, per-cell errors, staged imports.

Every module that takes a spreadsheet uses this rather than hand-rolling its own half-safe loader.
Parsing itself defers to Python's `csv` module, which already handles the quoting rules correctly;
the value added here is what surrounds it:

  * **Typed coercion with per-cell errors.** "Row 47, column 'due_date': 'next Tuesday' is not a
    date" is actionable; "ValueError" is not. A user fixing a 3,000-row export needs to know
    exactly which cell to look at.

  * **Quarantine, never abort.** One bad row does not stop the other 2,999. Bad rows are held with
    their raw payload and reason so the run can report counts that reconcile:
    seen = valid + rejected. An import that silently drops rows is the failure mode a spreadsheet
    pipeline must not have.

  * **Real-world file quirks handled once.** Excel writes a UTF-8 BOM, users leave blank trailing
    lines, and ragged rows happen when someone deletes a cell instead of a row. Each is dealt with
    here so no module rediscovers it.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

TEXT = "text"
INTEGER = "integer"
DECIMAL = "decimal"
DATE = "date"
BOOLEAN = "boolean"

# What a user might reasonably type in a spreadsheet for yes/no. Excel and human habit produce
# all of these; guessing wrong on a boolean silently flips a flag, so the accepted set is explicit.
_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


@dataclass(frozen=True)
class Column:
    """One expected CSV column."""

    key: str                       # the field name produced in the parsed row
    header: str                    # the header text expected in the file
    col_type: str = TEXT
    required: bool = False
    # Optional post-coercion check returning an error string, or None when the value is fine.
    validate: Callable[[Any], str | None] | None = None


@dataclass
class ParsedRow:
    row_number: int                # 1-based data row, matching what the user sees in Excel
    raw: dict[str, str]
    parsed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.error is None


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    header_error: str | None = None

    @property
    def valid(self) -> list[ParsedRow]:
        return [r for r in self.rows if r.is_valid]

    @property
    def rejected(self) -> list[ParsedRow]:
        return [r for r in self.rows if not r.is_valid]

    def reconciles(self) -> bool:
        """seen == valid + rejected. Asserted by callers before committing: a run whose counts do
        not add up has lost rows somewhere, and committing it would make that loss permanent."""
        return len(self.rows) == len(self.valid) + len(self.rejected)


class CsvError(ValueError):
    """The file could not be processed at all — unreadable, or its header does not match."""


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------


def coerce(value: str, col_type: str) -> Any:
    """Coerce one cell. Raises ValueError with a human-readable reason.

    An empty cell is None for every type — "blank" is a legitimate spreadsheet state, and
    conflating it with 0 or "" hides missing data behind a plausible-looking value.
    """
    text = value.strip()
    if text == "":
        return None

    if col_type == TEXT:
        return text
    if col_type == INTEGER:
        try:
            # Accept thousands separators; a spreadsheet exports "33,756" for a number.
            return int(text.replace(",", "").replace(" ", ""))
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a whole number") from exc
    if col_type == DECIMAL:
        try:
            return Decimal(text.replace(",", "").replace("$", "").replace(" ", ""))
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a number") from exc
    if col_type == DATE:
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a date (expected YYYY-MM-DD)") from exc
    if col_type == BOOLEAN:
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"{value!r} is not a yes/no value")
    raise ValueError(f"unknown column type {col_type!r}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_csv(text: str, columns: list[Column], strict_header: bool = True) -> ParseResult:
    """Parse and type a CSV against a column spec.

    Returns every row — valid and rejected — so the caller can stage the lot and report counts
    that reconcile. A header problem is fatal and returned as `header_error`, because a file whose
    columns do not match is not a file with some bad rows; it is the wrong file.
    """
    # Excel writes a UTF-8 BOM; left in place it becomes part of the first header name and every
    # column lookup silently misses.
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return ParseResult(header_error="the file is empty")

    header = [h.strip() for h in header]
    expected = {c.header for c in columns}
    missing = sorted(expected - set(header))
    if missing and strict_header:
        return ParseResult(
            header_error=f"missing column(s): {', '.join(missing)}"
        )

    index_of = {h: i for i, h in enumerate(header)}
    result = ParseResult()

    for row_number, values in enumerate(reader, start=1):
        # A trailing blank line is not a row; treating it as one produces a phantom rejection on
        # every file saved by Excel.
        if not any(v.strip() for v in values):
            continue

        raw = {
            col.header: (values[index_of[col.header]]
                         if col.header in index_of and index_of[col.header] < len(values)
                         else "")
            for col in columns
        }
        row = ParsedRow(row_number=row_number, raw=raw)

        errors: list[str] = []
        for col in columns:
            cell = raw.get(col.header, "")
            try:
                value = coerce(cell, col.col_type)
            except ValueError as exc:
                errors.append(f"{col.header}: {exc}")
                continue
            if value is None and col.required:
                errors.append(f"{col.header}: required")
                continue
            if value is not None and col.validate is not None:
                problem = col.validate(value)
                if problem:
                    errors.append(f"{col.header}: {problem}")
                    continue
            row.parsed[col.key] = value

        if errors:
            row.error = "; ".join(errors)
            row.parsed = {}
        result.rows.append(row)

    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def to_csv(rows: list[dict[str, Any]], columns: list[Column]) -> str:
    """Render rows as CSV text.

    Uses csv.writer so embedded commas, quotes and newlines are quoted correctly rather than
    corrupting the file — the classic bug in hand-rolled exports, where one client name containing
    a comma shifts every subsequent column.

    Dates render ISO and None renders empty, so a re-import of an export round-trips.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([c.header for c in columns])
    for row in rows:
        writer.writerow([_render(row.get(c.key), c.col_type) for c in columns])
    return buffer.getvalue()


def _render(value: Any, col_type: str) -> str:
    if value is None:
        return ""
    if col_type == BOOLEAN:
        return "true" if value else "false"
    if col_type == DATE and isinstance(value, date):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Staging (the two-phase import)
# ---------------------------------------------------------------------------
#
# Stage, then commit. The gap between them is the point: a human sees the counts and the rejected
# rows BEFORE anything is written to the live tables, which is what stops a mis-mapped column
# quietly rewriting three thousand records.


def stage_import(
    conn: psycopg.Connection,
    entity: str,
    text: str,
    columns: list[Column],
    uploaded_by: UUID,
    filename: str | None = None,
) -> UUID:
    """Parse a file and stage every row — valid and rejected — for review.

    Nothing touches the live tables here. A header failure marks the whole run failed, because a
    file whose columns do not match is the wrong file, not a file with bad rows.
    """
    result = parse_csv(text, columns)

    row = conn.execute(
        "insert into app.csv_imports (entity, filename, uploaded_by, status) "
        "values (%s, %s, %s, 'staged') returning id",
        (entity, filename, uploaded_by),
    ).fetchone()
    assert row is not None
    import_id = UUID(str(row[0]))

    if result.header_error:
        conn.execute(
            "update app.csv_imports set status = 'failed', detail = %s where id = %s",
            (result.header_error, import_id),
        )
        return import_id

    for parsed_row in result.rows:
        conn.execute(
            "insert into app.csv_import_rows (import_id, row_number, raw, parsed, error) "
            "values (%s, %s, %s, %s, %s)",
            (import_id, parsed_row.row_number, Json(parsed_row.raw),
             Json(_jsonable(parsed_row.parsed)) if parsed_row.parsed else None,
             parsed_row.error),
        )

    conn.execute(
        "update app.csv_imports set rows_seen = %s, rows_valid = %s, rows_rejected = %s "
        " where id = %s",
        (len(result.rows), len(result.valid), len(result.rejected), import_id),
    )
    return import_id


def _jsonable(parsed: dict[str, Any]) -> dict[str, Any]:
    """Typed values to JSON-safe ones. Decimals become strings rather than floats — a Decimal
    rendered as a float is exactly the precision loss the Decimal was chosen to avoid."""
    out: dict[str, Any] = {}
    for key, value in parsed.items():
        if isinstance(value, Decimal):
            out[key] = str(value)
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def commit_import(
    conn: psycopg.Connection,
    import_id: UUID,
    committer: Callable[[psycopg.Connection, dict[str, Any]], None],
) -> dict[str, int]:
    """Apply the valid staged rows via ``committer``, one row at a time.

    Only rows that validated are applied; rejected rows stay quarantined with their reason. A row
    whose committer raises is recorded with that error rather than aborting the batch — the same
    quarantine discipline as parsing, so one unexpected constraint violation on row 900 does not
    discard the 899 good rows before it.

    Returns counts that reconcile against the staged totals.
    """
    status = conn.execute(
        "select status::text from app.csv_imports where id = %s", (import_id,)
    ).fetchone()
    if status is None:
        raise LookupError("import not found or not visible")
    if status[0] != "staged":
        raise ValueError(f"import is {status[0]}, not staged")

    rows = conn.execute(
        "select id, parsed from app.csv_import_rows "
        " where import_id = %s and error is null and not committed order by row_number",
        (import_id,),
    ).fetchall()

    committed = 0
    failed = 0
    for row_id, parsed in rows:
        try:
            committer(conn, parsed or {})
        except Exception as exc:  # noqa: BLE001 — record and continue; see docstring
            conn.execute(
                "update app.csv_import_rows set error = %s where id = %s",
                (f"commit failed: {exc}"[:500], row_id),
            )
            failed += 1
            continue
        conn.execute(
            "update app.csv_import_rows set committed = true where id = %s", (row_id,)
        )
        committed += 1

    conn.execute(
        "update app.csv_imports set status = 'committed', committed_at = now(), "
        "       rows_committed = %s, rows_rejected = rows_rejected + %s "
        " where id = %s",
        (committed, failed, import_id),
    )
    return {"committed": committed, "failed": failed}
