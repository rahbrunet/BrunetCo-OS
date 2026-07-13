"""WP 1.3 pure import + summary logic — no DB, exhaustively unit-testable (M1-R3/M1-R4)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from py_shared.domain.rule_import import (
    LadderStub,
    MappedRule,
    Unresolved,
    classify_rows,
    map_row,
    parse_offset,
)
from py_shared.domain.rule_summary import summarize

FIXTURE = Path(__file__).parent / "fixtures" / "appcoll_task_types_sample.csv"


def _rows() -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(FIXTURE.read_text(encoding="utf-8-sig"))))


# --- offset parsing ----------------------------------------------------------

def test_parse_offset_variants() -> None:
    assert parse_offset("4m") == {"months": 4}
    assert parse_offset("14d") == {"days": 14}
    assert parse_offset("2y6m") == {"years": 2, "months": 6}
    assert parse_offset("") is None
    assert parse_offset("garbage") is None


# --- whole-file classification (nothing dropped) -----------------------------

def test_classify_buckets_every_row() -> None:
    rows = _rows()
    s = classify_rows(rows)
    assert len(s.mapped) + len(s.unresolved) + len(s.ladder_stubs) == len(rows)
    assert len(s.mapped) == 9
    assert len(s.ladder_stubs) == 1
    assert len(s.unresolved) == 3


def test_deadline_type_counts_reconcile() -> None:
    # The acceptance analogue of the real 151/65/35/28/270/3 reconciliation.
    counts = classify_rows(_rows()).deadline_type_counts()
    assert counts == {
        "extendable_external": 2,
        "hard_external": 2,
        "internal": 1,
        "general_reminder": 1,
        "event": 2,
        "transient_event": 1,
    }


# --- specific mapping paths --------------------------------------------------

def _by_id(rows: list[dict[str, str]], tid: str) -> dict[str, str]:
    return next(r for r in rows if r["task_type_id"] == tid)


def test_dual_dates_and_extendable() -> None:
    m = map_row(_by_id(_rows(), "TT-1001"))
    assert isinstance(m, MappedRule)
    assert m.trigger_code == "office_action"
    assert m.jurisdiction_code == "CA"
    assert m.definition["offsets"] == {"respond_by": {"months": 4}, "final_due_date": {"months": 6}}
    assert m.active is True


def test_any_jurisdiction_rule_has_null_jurisdiction() -> None:
    m = map_row(_by_id(_rows(), "TT-1005"))
    assert isinstance(m, MappedRule)
    assert m.jurisdiction_code is None


def test_field_setter_action_captured() -> None:
    m = map_row(_by_id(_rows(), "TT-1004"))
    assert isinstance(m, MappedRule)
    assert m.definition["actions"] == [
        {"type": "set_field", "field": "AllowanceDate", "expr": "{TriggeringTask.RefDate}"}
    ]


def test_dual_path_alternate_offset() -> None:
    m = map_row(_by_id(_rows(), "TT-1008"))
    assert isinstance(m, MappedRule)
    assert m.definition["alternate_offsets"] == {"final_due_date": {"months": 4}}


def test_auto_generate_off() -> None:
    m = map_row(_by_id(_rows(), "TT-1007"))
    assert isinstance(m, MappedRule)
    assert m.definition["auto_generate"] is False


def test_uspto_superseded_is_inactive_and_tagged() -> None:
    m = map_row(_by_id(_rows(), "TT-1009"))
    assert isinstance(m, MappedRule)
    assert m.active is False
    assert "superseded-by-a1" in m.import_tags


def test_reminder_pair_becomes_ladder_stub() -> None:
    r = map_row(_by_id(_rows(), "TT-1010"))
    assert isinstance(r, LadderStub)
    assert r.reminder_of == "Pay Maintenance Fee"


def test_missing_trigger_is_unresolved() -> None:
    r = map_row(_by_id(_rows(), "TT-1011"))
    assert isinstance(r, Unresolved)
    assert "trigger" in r.reason.lower()


def test_unknown_deadline_type_is_unresolved() -> None:
    r = map_row(_by_id(_rows(), "TT-1012"))
    assert isinstance(r, Unresolved)
    assert "deadline_type" in r.reason


def test_no_offsets_is_unresolved() -> None:
    r = map_row(_by_id(_rows(), "TT-1013"))
    assert isinstance(r, Unresolved)
    assert "offset" in r.reason.lower()


# --- summary generation ------------------------------------------------------

def test_summary_reads_naturally() -> None:
    m = map_row(_by_id(_rows(), "TT-1001"))
    assert isinstance(m, MappedRule)
    text = summarize(m.definition, m.trigger_code, m.jurisdiction_code)
    assert "office action occurs" in text
    assert "CA" in text
    assert "Respond to Office Action" in text
    assert "4 months" in text
    assert "extendable" in text.lower()


def test_summary_includes_field_action_note() -> None:
    m = map_row(_by_id(_rows(), "TT-1004"))
    assert isinstance(m, MappedRule)
    text = summarize(m.definition, m.trigger_code, m.jurisdiction_code)
    assert "AllowanceDate" in text
