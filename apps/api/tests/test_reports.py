"""Report Builder query construction (WP 5B.1) — pure, no DB.

The bulk of these are refusals. The builder's job is to be unable to express a query the dataset
registry did not sanction, so the tests that matter are the ones proving a hostile definition
cannot get through — including one that has been hand-edited in the stored JSON.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from py_shared.domain.reports import (
    Aggregate,
    Definition,
    ReportDefinitionError,
    build_query,
    describe_datasets,
    is_due,
    output_columns,
)


def _sql(definition: Definition) -> str:
    return build_query(definition)[0]


# --- the happy paths -----------------------------------------------------------


def test_a_column_report_selects_registered_columns() -> None:
    sql, params = build_query(Definition("matters", columns=["reference", "status"]))
    assert sql == "select reference, status from app.matters limit 1000"
    assert params == []


def test_filters_are_bound_never_interpolated() -> None:
    sql, params = build_query(Definition(
        "tasks", columns=["title"], filters=[{"column": "status", "op": "eq", "value": "open"}],
    ))
    assert "status = %s" in sql
    assert params == ["open"]
    assert "open" not in sql


def test_contains_wraps_the_value_not_the_sql() -> None:
    sql, params = build_query(Definition(
        "matters", columns=["reference"],
        filters=[{"column": "reference", "op": "contains", "value": "CA"}],
    ))
    assert "reference ilike %s" in sql
    assert params == ["%CA%"]


def test_null_operators_take_no_parameter() -> None:
    sql, params = build_query(Definition(
        "tasks", columns=["title"], filters=[{"column": "closed_on", "op": "is_null"}],
    ))
    assert "closed_on is null" in sql
    assert params == []


def test_in_filter_binds_a_list() -> None:
    _, params = build_query(Definition(
        "tasks", columns=["title"],
        filters=[{"column": "status", "op": "in", "value": ["open", "missed"]}],
    ))
    assert params == [["open", "missed"]]


def test_sorting_descending_uses_a_leading_minus() -> None:
    assert "order by final_due_date desc" in _sql(
        Definition("tasks", columns=["title"], sort=["-final_due_date"])
    )


def test_a_grouped_report_counts_by_its_keys() -> None:
    sql = _sql(Definition(
        "tasks", group_by=["status"], aggregates=[Aggregate("count")], sort=["-count"],
    ))
    assert sql == (
        "select status, count(*) as count from app.tasks group by status "
        "order by count desc limit 1000"
    )


def test_output_columns_describe_the_result_shape() -> None:
    assert output_columns(
        Definition("tasks", group_by=["status"], aggregates=[Aggregate("count")])
    ) == ["status", "count"]


# --- refusals: the security core -----------------------------------------------


def test_an_unknown_dataset_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="unknown dataset"):
        build_query(Definition("pg_shadow", columns=["passwd"]))


def test_an_unregistered_column_is_refused() -> None:
    """Names are matched against the registry, not escaped — so this cannot reach SQL."""
    with pytest.raises(ReportDefinitionError, match="no field"):
        build_query(Definition("matters", columns=["family_id"]))


def test_an_injection_attempt_in_a_column_name_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="no field"):
        build_query(Definition("matters", columns=["reference from app.os_users --"]))


def test_an_injection_attempt_in_a_filter_column_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="no field"):
        build_query(Definition(
            "matters", columns=["reference"],
            filters=[{"column": "1=1 or reference", "op": "eq", "value": "x"}],
        ))


def test_an_injection_attempt_in_a_sort_key_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="no field"):
        build_query(Definition("matters", columns=["reference"], sort=["reference; drop table x"]))


def test_an_unknown_operator_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="unknown operator"):
        build_query(Definition(
            "matters", columns=["reference"],
            filters=[{"column": "reference", "op": "; drop table", "value": "x"}],
        ))


def test_a_hand_edited_definition_still_cannot_escape_the_registry() -> None:
    """Definitions live in a jsonb column an admin can edit. Round-tripping one through
    from_json must not be a way around validation."""
    hostile = Definition.from_json({
        "dataset": "matters",
        "columns": ["reference"],
        "sort": ["(select passwd from pg_shadow)"],
    })
    with pytest.raises(ReportDefinitionError):
        build_query(hostile)


def test_a_report_needs_at_least_one_column() -> None:
    with pytest.raises(ReportDefinitionError, match="at least one column"):
        build_query(Definition("matters"))


def test_a_grouped_report_may_not_also_select_free_columns() -> None:
    """Otherwise Postgres rejects it at run time with a grouping error the user cannot act on."""
    with pytest.raises(ReportDefinitionError, match="not free columns"):
        build_query(Definition(
            "tasks", columns=["title"], group_by=["status"], aggregates=[Aggregate("count")],
        ))


def test_a_grouped_report_may_only_sort_by_its_own_output() -> None:
    with pytest.raises(ReportDefinitionError, match="own output columns"):
        build_query(Definition("tasks", group_by=["status"], aggregates=[Aggregate("count")],
                               sort=["title"]))


def test_summing_a_non_numeric_field_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="not a numeric field"):
        build_query(Definition("tasks", group_by=["status"],
                               aggregates=[Aggregate("sum", "title")]))


def test_an_unknown_aggregate_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="unknown aggregate"):
        build_query(Definition("tasks", group_by=["status"], aggregates=[Aggregate("system")]))


def test_contains_on_a_date_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="only applies to text"):
        build_query(Definition(
            "tasks", columns=["title"],
            filters=[{"column": "final_due_date", "op": "contains", "value": "2026"}],
        ))


def test_a_filter_without_a_value_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="needs a value"):
        build_query(Definition(
            "tasks", columns=["title"], filters=[{"column": "status", "op": "eq"}],
        ))


def test_an_empty_in_list_is_refused() -> None:
    """`in ()` is not a filter, it is a way to get an empty report and not know why."""
    with pytest.raises(ReportDefinitionError, match="non-empty list"):
        build_query(Definition(
            "tasks", columns=["title"],
            filters=[{"column": "status", "op": "in", "value": []}],
        ))


def test_a_malformed_filter_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="malformed filter"):
        build_query(Definition("tasks", columns=["title"], filters=[{"col": "status"}]))


def test_an_absurd_limit_is_refused() -> None:
    with pytest.raises(ReportDefinitionError, match="limit must be"):
        build_query(Definition("matters", columns=["reference"], limit=10_000_000))
    with pytest.raises(ReportDefinitionError, match="limit must be"):
        build_query(Definition("matters", columns=["reference"], limit=0))


# --- definition round-trip -----------------------------------------------------


def test_a_definition_survives_a_json_round_trip() -> None:
    original = Definition(
        "tasks", group_by=["status", "task_type"], aggregates=[Aggregate("count")],
        filters=[{"column": "closed_on", "op": "not_null"}], sort=["-count"], limit=50,
    )
    assert build_query(Definition.from_json(original.to_json())) == build_query(original)


def test_the_registry_describes_itself_for_the_builder_ui() -> None:
    described = {d["key"] for d in describe_datasets()}
    assert described == {"matters", "tasks", "work_items"}


# --- scheduling ----------------------------------------------------------------

MON_8AM = datetime(2026, 7, 20, 8, 0)


def test_an_unscheduled_report_is_never_due() -> None:
    assert not is_due(None, 7, None, MON_8AM)


def test_a_scheduled_report_that_never_ran_is_due_once_its_hour_arrives() -> None:
    assert not is_due("daily", 7, None, datetime(2026, 7, 20, 6, 0))
    assert is_due("daily", 7, None, MON_8AM)


def test_a_daily_report_is_not_due_twice_in_one_day() -> None:
    assert not is_due("daily", 7, datetime(2026, 7, 20, 7, 5), MON_8AM)


def test_a_weekly_report_waits_a_full_week() -> None:
    assert not is_due("weekly", 7, datetime(2026, 7, 15, 7, 0), MON_8AM)
    assert is_due("weekly", 7, datetime(2026, 7, 13, 7, 0), MON_8AM)


def test_a_missed_window_runs_late_rather_than_being_skipped() -> None:
    """A weekly report nobody received is worse than one that arrives on Tuesday."""
    assert is_due("weekly", 7, datetime(2026, 6, 1, 7, 0), MON_8AM)


def test_an_unknown_frequency_never_fires() -> None:
    assert not is_due("hourly", 7, None, MON_8AM)
