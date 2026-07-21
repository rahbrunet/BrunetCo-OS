"""Report Builder core (WP 5B.1, spec §11B) — user-defined reports over registered datasets.

Reports are deliverables, not dashboards: a saved definition (columns, filters, grouping, sort)
that can be shared with the firm, run on demand, and run on a schedule.

Three properties the design turns on:

  * **No free SQL, ever.** A report definition names a dataset, and every column, filter target,
    grouping key and sort key must resolve against that dataset's registered spec. Values are
    always bound parameters. A definition that references anything unregistered raises rather than
    reaching the database — the builder cannot express a query the registry did not sanction.

  * **A shared report is a shared *definition*, not shared data.** Runs execute on the viewer's own
    RLS-scoped connection (D44), so two people running the same report see their own permitted
    rows. Sharing a report can therefore never leak a matter the recipient could not already open.

  * **Scheduling stores intent, not a cron string.** Frequency plus hour is enough for "the Monday
    docket report", is checkable without a parser, and cannot express a schedule the runner does
    not understand.

Delivery (PDF/spreadsheet by email, SFTP optional) is deliberately not here: a run produces rows
plus a spreadsheet rendering, and transport lands with the Graph work in WP 4.3. What is durable
today is the definition, the run record and the row count.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from py_shared.domain.csv_io import Column as CsvColumn
from py_shared.domain.csv_io import to_csv

# Column types, reused from the CSV framework so a report exports without a second type system.
TEXT, INTEGER, DECIMAL, DATE, BOOLEAN = "text", "integer", "decimal", "date", "boolean"
TIMESTAMP = "timestamp"


class ReportDefinitionError(ValueError):
    """A definition the builder refuses to turn into SQL."""


@dataclass(frozen=True)
class Field:
    """One reportable column of a dataset."""

    key: str                       # the caller-facing name AND the underlying column name
    label: str
    col_type: str = TEXT
    filterable: bool = True
    groupable: bool = True
    aggregatable: bool = False     # sum/avg only make sense on numbers


@dataclass(frozen=True)
class Dataset:
    """A whitelisted queryable surface. `source` is interpolated into SQL, so it is never
    caller-supplied — it comes from this module's registry and nowhere else."""

    key: str
    label: str
    source: str                    # 'app.matters', a view, …
    fields: tuple[Field, ...]

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise ReportDefinitionError(f"{self.key!r} has no field {key!r}")


DATASETS: dict[str, Dataset] = {
    "matters": Dataset(
        key="matters", label="Matters", source="app.matters",
        fields=(
            Field("reference", "Reference"),
            Field("jurisdiction_code", "Jurisdiction"),
            Field("jurisdiction_segment", "Segment"),
            Field("status", "Status"),
            Field("application_no", "Application no."),
            Field("registration_no", "Registration no."),
            Field("filing_date", "Filed", DATE),
            Field("registration_date", "Registered", DATE),
            Field("small_entity", "Small entity", BOOLEAN),
            Field("responsible_user_id", "Responsible", TEXT, groupable=True),
            Field("created_at", "Created", TIMESTAMP),
        ),
    ),
    "tasks": Dataset(
        key="tasks", label="Docket tasks", source="app.tasks",
        fields=(
            Field("title", "Title"),
            Field("task_type", "Task type"),
            Field("deadline_type", "Deadline type"),
            Field("status", "Status"),
            Field("awaiting", "Awaiting"),
            Field("ref_date", "Reference date", DATE),
            Field("respond_by", "Respond by", DATE),
            Field("final_due_date", "Final due", DATE),
            Field("closed_on", "Closed", DATE),
            Field("assignee_id", "Assignee"),
            Field("matter_id", "Matter"),
            Field("created_at", "Created", TIMESTAMP),
        ),
    ),
    "work_items": Dataset(
        key="work_items", label="Work items", source="app.work_items",
        fields=(
            Field("title", "Title"),
            Field("status", "Status"),
            Field("due_date", "Due", DATE),
            Field("assignee_id", "Assignee"),
            Field("matter_id", "Matter"),
            Field("stage_name", "Stage"),
            Field("role", "Role"),
            Field("started_on", "Started", DATE),
            Field("completed_on", "Completed", DATE),
            Field("created_at", "Created", TIMESTAMP),
        ),
    ),
}

# Comparison operators a filter may use, mapped to their SQL. `contains` is case-insensitive
# because a user filtering references for "ca" means the segment, not the byte sequence.
_OPS: dict[str, str] = {
    "eq": "= %s",
    "ne": "<> %s",
    "lt": "< %s",
    "lte": "<= %s",
    "gt": "> %s",
    "gte": ">= %s",
    "contains": "ilike %s",
    "in": "= any(%s)",
    "is_null": "is null",
    "not_null": "is not null",
}
_NO_VALUE = {"is_null", "not_null"}

_AGGREGATES = {"count", "sum", "avg", "min", "max"}


@dataclass
class Aggregate:
    func: str
    column: str | None = None      # None only for count(*)

    @property
    def alias(self) -> str:
        return f"{self.func}_{self.column}" if self.column else "count"


@dataclass
class Definition:
    """What a saved report is. Round-trips through JSON without loss."""

    dataset: str
    columns: list[str] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    aggregates: list[Aggregate] = field(default_factory=list)
    sort: list[str] = field(default_factory=list)   # 'column' or '-column' for descending
    limit: int = 1000

    def to_json(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset, "columns": self.columns, "filters": self.filters,
            "group_by": self.group_by, "sort": self.sort, "limit": self.limit,
            "aggregates": [{"func": a.func, "column": a.column} for a in self.aggregates],
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Definition:
        return Definition(
            dataset=raw["dataset"],
            columns=list(raw.get("columns", [])),
            filters=list(raw.get("filters", [])),
            group_by=list(raw.get("group_by", [])),
            aggregates=[Aggregate(a["func"], a.get("column")) for a in raw.get("aggregates", [])],
            sort=list(raw.get("sort", [])),
            limit=int(raw.get("limit", 1000)),
        )


MAX_ROWS = 10_000


# ---------------------------------------------------------------------------
# Query building (pure) — the security core
# ---------------------------------------------------------------------------

def _check_value(field_: Field, op: str, value: Any) -> Any:
    if op in _NO_VALUE:
        return None
    if value is None:
        raise ReportDefinitionError(f"filter on {field_.key!r} with {op!r} needs a value")
    if op == "in":
        if not isinstance(value, list) or not value:
            raise ReportDefinitionError(f"'in' filter on {field_.key!r} needs a non-empty list")
        return value
    if op == "contains":
        if field_.col_type != TEXT:
            raise ReportDefinitionError(f"'contains' only applies to text, not {field_.key!r}")
        return f"%{value}%"
    return value


def build_query(definition: Definition) -> tuple[str, list[Any]]:
    """Turn a definition into (sql, params), or raise.

    Every identifier that reaches the SQL string is looked up in the dataset registry first, so a
    caller cannot introduce one: names are checked by identity against registered fields, not
    escaped or sanitised. Values are always bound. That is why a definition arriving from a stored
    JSON blob — which an admin may have edited — is safe to execute.
    """
    dataset = DATASETS.get(definition.dataset)
    if dataset is None:
        raise ReportDefinitionError(f"unknown dataset {definition.dataset!r}")
    if definition.limit < 1 or definition.limit > MAX_ROWS:
        raise ReportDefinitionError(f"limit must be between 1 and {MAX_ROWS}")

    grouped = bool(definition.group_by) or bool(definition.aggregates)
    select: list[str] = []
    params: list[Any] = []

    if grouped:
        if definition.columns:
            raise ReportDefinitionError(
                "a grouped report selects its grouping keys and aggregates, not free columns"
            )
        for key in definition.group_by:
            f = dataset.field(key)
            if not f.groupable:
                raise ReportDefinitionError(f"{key!r} cannot be grouped on")
            select.append(f.key)
        for agg in definition.aggregates:
            if agg.func not in _AGGREGATES:
                raise ReportDefinitionError(f"unknown aggregate {agg.func!r}")
            if agg.func == "count" and agg.column is None:
                select.append("count(*) as count")
                continue
            if agg.column is None:
                raise ReportDefinitionError(f"{agg.func!r} needs a column")
            f = dataset.field(agg.column)
            if agg.func in ("sum", "avg") and not f.aggregatable:
                raise ReportDefinitionError(f"{f.key!r} is not a numeric field")
            select.append(f"{agg.func}({f.key}) as {agg.alias}")
        if not select:
            raise ReportDefinitionError("a grouped report needs at least one grouping or aggregate")
    else:
        if not definition.columns:
            raise ReportDefinitionError("a report needs at least one column")
        select = [dataset.field(k).key for k in definition.columns]

    where: list[str] = []
    for spec in definition.filters:
        try:
            key, op = spec["column"], spec["op"]
        except (KeyError, TypeError) as exc:
            raise ReportDefinitionError(f"malformed filter: {spec!r}") from exc
        if op not in _OPS:
            raise ReportDefinitionError(f"unknown operator {op!r}")
        f = dataset.field(key)
        if not f.filterable:
            raise ReportDefinitionError(f"{key!r} cannot be filtered on")
        value = _check_value(f, op, spec.get("value"))
        where.append(f"{f.key} {_OPS[op]}")
        if op not in _NO_VALUE:
            params.append(value)

    order: list[str] = []
    valid_sort = {s.split(" as ")[-1] for s in select} if grouped else None
    for key in definition.sort:
        descending = key.startswith("-")
        bare = key[1:] if descending else key
        if grouped:
            if valid_sort is None or bare not in valid_sort:
                raise ReportDefinitionError(f"a grouped report can only sort by its own output "
                                            f"columns, not {bare!r}")
            resolved = bare
        else:
            resolved = dataset.field(bare).key
        order.append(f"{resolved} {'desc' if descending else 'asc'}")

    sql = f"select {', '.join(select)} from {dataset.source}"  # noqa: S608 — registry-only idents
    if where:
        sql += " where " + " and ".join(where)
    if grouped and definition.group_by:
        sql += " group by " + ", ".join(dataset.field(k).key for k in definition.group_by)
    if order:
        sql += " order by " + ", ".join(order)
    sql += f" limit {int(definition.limit)}"
    return sql, params


def output_columns(definition: Definition) -> list[str]:
    """The column names a run of this definition produces, in order."""
    sql, _ = build_query(definition)
    head = sql[len("select "):sql.index(" from ")]
    return [part.split(" as ")[-1].strip() for part in head.split(", ")]


# ---------------------------------------------------------------------------
# Saving, sharing, running
# ---------------------------------------------------------------------------

def save_report(
    conn: psycopg.Connection, owner_id: UUID, name: str, definition: Definition,
    shared: bool = False, schedule_frequency: str | None = None, schedule_hour: int = 7,
) -> UUID:
    """Persist a report, validating the definition first.

    Validation happens before the insert so an unrunnable report can never be saved, shared and
    then fail for whoever opens it later.
    """
    build_query(definition)
    if schedule_frequency is not None and schedule_frequency not in ("daily", "weekly", "monthly"):
        raise ReportDefinitionError(f"unknown schedule frequency {schedule_frequency!r}")
    if not 0 <= schedule_hour <= 23:
        raise ReportDefinitionError("schedule hour must be 0-23")
    row = conn.execute(
        """
        insert into app.reports
          (owner_id, name, dataset_key, definition, shared, schedule_frequency, schedule_hour)
        values (%s, %s, %s, %s, %s, %s, %s) returning id
        """,
        (owner_id, name, definition.dataset, json.dumps(definition.to_json()), shared,
         schedule_frequency, schedule_hour),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def load_definition(conn: psycopg.Connection, report_id: UUID) -> Definition:
    row = conn.execute(
        "select definition from app.reports where id = %s", (report_id,),
    ).fetchone()
    if row is None:
        raise LookupError("report not found or not visible")
    raw = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return Definition.from_json(raw)


@dataclass
class RunResult:
    run_id: UUID
    columns: list[str]
    rows: list[dict[str, Any]]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def run_report(conn: psycopg.Connection, report_id: UUID, requested_by: UUID) -> RunResult:
    """Execute a report on the caller's own connection and record the run.

    Running on the caller's connection is the whole security model for sharing: RLS filters the
    underlying rows to what *this* viewer may see, so a firm-shared report shows each person their
    own slice rather than the author's.
    """
    definition = load_definition(conn, report_id)
    sql, params = build_query(definition)
    cursor = conn.execute(sql, params)
    names = [d.name for d in cursor.description or []]
    rows = [dict(zip(names, r, strict=False)) for r in cursor.fetchall()]

    run = conn.execute(
        "insert into app.report_runs (report_id, requested_by, row_count, status) "
        "values (%s, %s, %s, 'ok') returning id",
        (report_id, requested_by, len(rows)),
    ).fetchone()
    assert run is not None
    return RunResult(run_id=UUID(str(run[0])), columns=names, rows=rows)


def to_spreadsheet(result: RunResult) -> str:
    """Render a run as CSV, reusing the WP 5B.2 writer so one value-rendering rule exists."""
    columns = [CsvColumn(key=c, header=c, col_type=TEXT) for c in result.columns]
    stringified = [
        {k: ("" if v is None else str(v)) for k, v in row.items()} for row in result.rows
    ]
    return to_csv(stringified, columns)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def is_due(
    frequency: str | None, schedule_hour: int, last_run: datetime | None, now: datetime,
) -> bool:
    """Whether a scheduled report should run at `now`. Pure, so the runner is testable.

    Deliberately conservative: a report is due only once its interval has fully elapsed since the
    last run, and never before its hour on the day. A missed window runs late rather than being
    skipped — a weekly report nobody received is a worse failure than one that arrives on Tuesday.
    """
    # Interval first, so an unrecognised frequency never fires — not even on the never-run path.
    intervals = {"daily": 1, "weekly": 7, "monthly": 28}
    interval = intervals.get(frequency or "")
    if interval is None:
        return False
    if now.hour < schedule_hour:
        return False
    if last_run is None:
        return True
    return (now.date() - last_run.date()).days >= interval


def due_reports(conn: psycopg.Connection, now: datetime | None = None) -> list[UUID]:
    """Scheduled reports whose window has come, most overdue first."""
    now = now or datetime.now().astimezone()
    rows = conn.execute(
        """
        select r.id, r.schedule_frequency, r.schedule_hour, max(x.run_at)
          from app.reports r
          left join app.report_runs x on x.report_id = r.id and x.status = 'ok'
         where r.schedule_frequency is not null and r.active
         group by r.id, r.schedule_frequency, r.schedule_hour
         order by max(x.run_at) nulls first
        """,
    ).fetchall()
    return [
        UUID(str(r[0])) for r in rows if is_due(r[1], r[2], r[3], now)
    ]


def record_failure(
    conn: psycopg.Connection, report_id: UUID, requested_by: UUID, error: str,
) -> None:
    """A failed run is still a run. A schedule that silently produces nothing looks identical to
    one that produced an empty report, and the two need to be distinguishable."""
    conn.execute(
        "insert into app.report_runs (report_id, requested_by, row_count, status, error) "
        "values (%s, %s, 0, 'failed', %s)",
        (report_id, requested_by, error[:2000]),
    )


def recent_runs(conn: psycopg.Connection, report_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select id, run_at, row_count, status, error from app.report_runs "
        " where report_id = %s order by run_at desc limit %s",
        (report_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "run_at": r[1], "row_count": r[2], "status": r[3], "error": r[4]}
        for r in rows
    ]


def list_reports(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Everything the caller can see: their own reports plus firm-shared ones (RLS decides)."""
    rows = conn.execute(
        "select id, name, dataset_key, owner_id, shared, schedule_frequency, schedule_hour, "
        " active from app.reports order by name",
    ).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "dataset_key": r[2], "owner_id": r[3], "shared": r[4],
            "schedule_frequency": r[5], "schedule_hour": r[6], "active": r[7],
        }
        for r in rows
    ]


def describe_datasets() -> list[dict[str, Any]]:
    """The registry, for the report-builder UI. There is nothing else a report may query."""
    return [
        {
            "key": d.key, "label": d.label,
            "fields": [
                {"key": f.key, "label": f.label, "type": f.col_type,
                 "filterable": f.filterable, "groupable": f.groupable,
                 "aggregatable": f.aggregatable}
                for f in d.fields
            ],
        }
        for d in DATASETS.values()
    ]


__all__ = [
    "DATASETS", "MAX_ROWS", "Aggregate", "Dataset", "Definition", "Field", "ReportDefinitionError",
    "RunResult", "build_query", "describe_datasets", "due_reports", "is_due",
    "list_reports", "load_definition", "output_columns", "recent_runs", "record_failure",
    "run_report", "save_report", "to_spreadsheet",
]
