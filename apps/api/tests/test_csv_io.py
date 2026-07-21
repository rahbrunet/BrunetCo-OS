"""CSV framework parsing, coercion and export (WP 5B.2) — pure, no DB.

CSV is a format with more edge cases than it looks like it has: BOMs, embedded commas, ragged
rows, blank trailing lines. Each one below is a real file a user will upload.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from py_shared.domain import csv_io

COLUMNS = [
    csv_io.Column("name", "Name", csv_io.TEXT, required=True),
    csv_io.Column("count", "Count", csv_io.INTEGER),
    csv_io.Column("amount", "Amount", csv_io.DECIMAL),
    csv_io.Column("due", "Due", csv_io.DATE),
    csv_io.Column("active", "Active", csv_io.BOOLEAN),
]


# --- coercion ------------------------------------------------------------------


def test_blank_is_none_for_every_type() -> None:
    """"Blank" is a legitimate spreadsheet state; conflating it with 0 or "" hides missing data."""
    for col_type in (csv_io.TEXT, csv_io.INTEGER, csv_io.DECIMAL, csv_io.DATE, csv_io.BOOLEAN):
        assert csv_io.coerce("   ", col_type) is None


def test_integers_accept_thousands_separators() -> None:
    """A spreadsheet exports 33,756 for a number."""
    assert csv_io.coerce("33,756", csv_io.INTEGER) == 33756


def test_decimals_accept_currency_formatting() -> None:
    assert csv_io.coerce("$1,234.56", csv_io.DECIMAL) == Decimal("1234.56")


def test_dates_parse_iso() -> None:
    assert csv_io.coerce("2026-07-20", csv_io.DATE) == date(2026, 7, 20)


@pytest.mark.parametrize("text", ["true", "TRUE", "Yes", "y", "1"])
def test_truthy_booleans(text: str) -> None:
    assert csv_io.coerce(text, csv_io.BOOLEAN) is True


@pytest.mark.parametrize("text", ["false", "No", "n", "0"])
def test_falsy_booleans(text: str) -> None:
    assert csv_io.coerce(text, csv_io.BOOLEAN) is False


def test_an_ambiguous_boolean_is_refused_rather_than_guessed() -> None:
    """Guessing wrong on a boolean silently flips a flag."""
    with pytest.raises(ValueError, match="yes/no"):
        csv_io.coerce("maybe", csv_io.BOOLEAN)


@pytest.mark.parametrize(
    "text,col_type,fragment",
    [
        ("lots", csv_io.INTEGER, "whole number"),
        ("abc", csv_io.DECIMAL, "not a number"),
        ("next Tuesday", csv_io.DATE, "not a date"),
    ],
)
def test_bad_values_report_what_is_wrong(text: str, col_type: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        csv_io.coerce(text, col_type)


# --- parsing -------------------------------------------------------------------


def test_a_clean_file_parses() -> None:
    text = "Name,Count,Amount,Due,Active\nAcme,5,100.50,2026-07-20,yes\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.header_error is None
    assert len(result.valid) == 1
    row = result.valid[0].parsed
    assert row == {"name": "Acme", "count": 5, "amount": Decimal("100.50"),
                   "due": date(2026, 7, 20), "active": True}


def test_a_utf8_bom_does_not_break_the_first_column() -> None:
    """Excel writes a BOM; left in place it becomes part of the first header name."""
    text = "﻿Name,Count,Amount,Due,Active\nAcme,1,,,\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.header_error is None
    assert result.valid[0].parsed["name"] == "Acme"


def test_embedded_commas_and_quotes_survive() -> None:
    text = 'Name,Count,Amount,Due,Active\n"Brunet, Smith & Co ""IP""",2,,,\n'
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.valid[0].parsed["name"] == 'Brunet, Smith & Co "IP"'


def test_a_trailing_blank_line_is_not_a_phantom_row() -> None:
    """Every file Excel saves ends this way; treating it as a row produces a phantom rejection."""
    text = "Name,Count,Amount,Due,Active\nAcme,1,,,\n\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert len(result.rows) == 1


def test_a_ragged_row_is_padded_not_crashed() -> None:
    """Someone deleted a cell instead of a row; the missing tail reads as blank."""
    text = "Name,Count,Amount,Due,Active\nAcme,1\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.valid[0].parsed["amount"] is None


def test_a_missing_header_column_is_fatal() -> None:
    """A file whose columns do not match is not a file with bad rows; it is the wrong file."""
    result = csv_io.parse_csv("Name,Count\nAcme,1\n", COLUMNS)
    assert result.header_error is not None
    assert "Amount" in result.header_error


def test_an_empty_file_is_reported_clearly() -> None:
    assert csv_io.parse_csv("", COLUMNS).header_error == "the file is empty"


def test_extra_columns_in_the_file_are_ignored() -> None:
    """Users export more columns than we need; that is not an error."""
    text = "Name,Count,Amount,Due,Active,Notes\nAcme,1,,,,hello\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.header_error is None
    assert result.valid[0].parsed["name"] == "Acme"


# --- quarantine, not abort -----------------------------------------------------


def test_one_bad_row_does_not_stop_the_others() -> None:
    text = (
        "Name,Count,Amount,Due,Active\n"
        "Good,1,,,\n"
        "Bad,not-a-number,,,\n"
        "AlsoGood,2,,,\n"
    )
    result = csv_io.parse_csv(text, COLUMNS)
    assert len(result.valid) == 2
    assert len(result.rejected) == 1


def test_a_rejected_row_keeps_its_raw_payload_and_reason() -> None:
    """The user needs to know exactly which cell to look at."""
    text = "Name,Count,Amount,Due,Active\nBad,oops,,,\n"
    rejected = csv_io.parse_csv(text, COLUMNS).rejected[0]
    assert rejected.raw["Count"] == "oops"
    assert "Count:" in rejected.error and "whole number" in rejected.error


def test_row_numbers_match_what_the_user_sees() -> None:
    text = "Name,Count,Amount,Due,Active\nA,1,,,\nB,bad,,,\n"
    assert csv_io.parse_csv(text, COLUMNS).rejected[0].row_number == 2


def test_all_errors_in_a_row_are_reported_together() -> None:
    """Fixing one cell per upload cycle is how people give up on an import."""
    text = "Name,Count,Amount,Due,Active\n,bad,alsobad,,\n"
    error = csv_io.parse_csv(text, COLUMNS).rejected[0].error
    assert "Name" in error and "Count" in error and "Amount" in error


def test_counts_reconcile() -> None:
    """seen == valid + rejected; a run whose counts do not add up has lost rows."""
    text = "Name,Count,Amount,Due,Active\nA,1,,,\nB,bad,,,\nC,3,,,\n"
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.reconciles()
    assert len(result.rows) == 3


def test_a_required_field_left_blank_is_rejected() -> None:
    text = "Name,Count,Amount,Due,Active\n,1,,,\n"
    assert "required" in csv_io.parse_csv(text, COLUMNS).rejected[0].error


def test_a_custom_validator_runs_after_coercion() -> None:
    cols = [
        csv_io.Column("count", "Count", csv_io.INTEGER,
                      validate=lambda v: "must be positive" if v <= 0 else None),
    ]
    result = csv_io.parse_csv("Count\n-5\n", cols)
    assert "must be positive" in result.rejected[0].error


# --- export --------------------------------------------------------------------


def test_export_writes_a_header_and_rows() -> None:
    out = csv_io.to_csv([{"name": "Acme", "count": 5}],
                        [csv_io.Column("name", "Name"), csv_io.Column("count", "Count",
                                                                      csv_io.INTEGER)])
    assert out == "Name,Count\nAcme,5\n"


def test_export_quotes_embedded_commas() -> None:
    """The classic hand-rolled-export bug: one client name with a comma shifts every column."""
    out = csv_io.to_csv([{"name": "Brunet, Smith"}], [csv_io.Column("name", "Name")])
    assert out == 'Name\n"Brunet, Smith"\n'


def test_export_renders_none_as_empty_and_dates_as_iso() -> None:
    cols = [csv_io.Column("due", "Due", csv_io.DATE), csv_io.Column("x", "X")]
    out = csv_io.to_csv([{"due": date(2026, 7, 20), "x": None}], cols)
    assert out == "Due,X\n2026-07-20,\n"


def test_an_export_round_trips_through_import() -> None:
    """The property that makes export/import a usable editing workflow."""
    rows = [{"name": "Acme, Inc", "count": 5, "amount": Decimal("10.25"),
             "due": date(2026, 7, 20), "active": True}]
    text = csv_io.to_csv(rows, COLUMNS)
    result = csv_io.parse_csv(text, COLUMNS)
    assert result.valid[0].parsed == rows[0]
