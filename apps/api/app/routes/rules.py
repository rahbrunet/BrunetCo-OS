"""Rule management + AppColl import (WP 1.3, M1-R3/M1-R4).

The approval-gated, versioned rule editor the WP 1.2 migration deferred here. All writes are
RLS-gated to the permissions admin (docket rules are firm-critical config — "no rule silently
changes"); a plain staff caller can read the library and run the simulator but cannot import,
edit, or approve.

Endpoints:
  GET  /api/v1/rules                      list the library (latest version each) + summary
  GET  /api/v1/rules/{rule_id}            one rule: latest version, summary, version history
  POST /api/v1/rules/import               upload an AppColl TaskTypes CSV → import (idempotent)
  GET  /api/v1/rules/unresolved           the manual-resolution queue (unmappable rows)
  POST /api/v1/rules/{rule_id}/simulate   dry-run against a matter, no persist (M1-R4 simulator)
  POST /api/v1/rules/{rule_id}/approve    activate a draft rule (approval gate)
  POST /api/v1/rules/{rule_id}/versions   edit → new version, draft/inactive until approved
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, UploadFile
from py_shared.domain.docketing import dry_run, rule_definition_is_valid
from py_shared.domain.rule_import import classify_rows
from py_shared.domain.rule_summary import summarize
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


class RuleOut(BaseModel):
    rule_id: UUID
    version: int
    name: str
    trigger_code: str
    jurisdiction_code: str | None
    definition: dict[str, Any]
    active: bool
    approval_status: str
    source: str
    appcoll_task_type_id: str | None
    import_tags: list[str]
    summary: str


class RuleDetailOut(RuleOut):
    versions: list[int]


class ImportOut(BaseModel):
    imported: int
    updated: int
    unresolved: int
    ladder_stubs: int
    deadline_type_counts: dict[str, int]
    superseded_by_a1: int
    total_rows: int


class UnresolvedOut(BaseModel):
    id: UUID
    appcoll_task_type_id: str | None
    reason: str
    raw: dict[str, Any]
    resolved: bool


class SimulateRequest(BaseModel):
    matter_id: UUID
    ref_date: date


class EditRequest(BaseModel):
    definition: dict[str, Any]


def _summ(trigger: str, jurisdiction: str | None, definition: dict[str, Any]) -> str:
    return summarize(definition, trigger, jurisdiction)


_LATEST_SQL = """
select distinct on (rule_id)
       rule_id, version, name, trigger_code, jurisdiction_code, definition, active,
       approval_status::text, source::text, appcoll_task_type_id, import_tags
  from app.docket_rules
 order by rule_id, version desc
"""


@router.get("", response_model=list[RuleOut])
def list_rules(identity: Identity) -> list[RuleOut]:
    with identity.connection() as conn:
        rows = conn.execute(_LATEST_SQL).fetchall()
    return [
        RuleOut(
            rule_id=r[0], version=r[1], name=r[2], trigger_code=r[3], jurisdiction_code=r[4],
            definition=r[5], active=r[6], approval_status=r[7], source=r[8],
            appcoll_task_type_id=r[9], import_tags=r[10], summary=_summ(r[3], r[4], r[5]),
        )
        for r in rows
    ]


@router.get("/unresolved", response_model=list[UnresolvedOut])
def unresolved_queue(identity: Identity, include_resolved: bool = False) -> list[UnresolvedOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, appcoll_task_type_id, reason, raw, resolved
              from app.rule_import_unresolved
             where (%s or not resolved)
             order by created_at
            """,
            (include_resolved,),
        ).fetchall()
    return [
        UnresolvedOut(id=r[0], appcoll_task_type_id=r[1], reason=r[2], raw=r[3], resolved=r[4])
        for r in rows
    ]


@router.get("/{rule_id}", response_model=RuleDetailOut)
def get_rule(rule_id: UUID, identity: Identity) -> RuleDetailOut:
    with identity.connection() as conn:
        latest = conn.execute(
            """
            select rule_id, version, name, trigger_code, jurisdiction_code, definition, active,
                   approval_status::text, source::text, appcoll_task_type_id, import_tags
              from app.docket_rules where rule_id = %s order by version desc limit 1
            """,
            (rule_id,),
        ).fetchone()
        if latest is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        versions = [
            r[0] for r in conn.execute(
                "select version from app.docket_rules where rule_id = %s order by version",
                (rule_id,),
            ).fetchall()
        ]
    return RuleDetailOut(
        rule_id=latest[0], version=latest[1], name=latest[2], trigger_code=latest[3],
        jurisdiction_code=latest[4], definition=latest[5], active=latest[6],
        approval_status=latest[7], source=latest[8], appcoll_task_type_id=latest[9],
        import_tags=latest[10], summary=_summ(latest[3], latest[4], latest[5]), versions=versions,
    )


@router.post("/import", response_model=ImportOut)
async def import_csv(file: UploadFile, identity: Identity) -> ImportOut:
    """Import an AppColl TaskTypes CSV. Idempotent: a row whose appcoll_task_type_id already
    exists updates that rule's v1 definition rather than duplicating (re-runnable, M1-R3).
    Imported rules land inactive + draft (except already-inactive superseded-by-a1 rows) so the
    engine never fires an unapproved rule; approval is a separate explicit step."""
    content = (await file.read()).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    summary = classify_rows(rows)

    imported = 0
    updated = 0
    try:
        with identity.connection() as conn:
            for m in summary.mapped:
                # Imported rules always land as draft/inactive pending approval (M1-R4), except
                # USPTO-superseded rows which are intentionally inactive already.
                approval = "draft"
                existing = conn.execute(
                    "select rule_id from app.docket_rules "
                    "where appcoll_task_type_id = %s and version = 1",
                    (m.appcoll_task_type_id,),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        update app.docket_rules
                           set name = %s, trigger_code = %s, jurisdiction_code = %s,
                               definition = %s, import_tags = %s, active = false,
                               approval_status = 'draft', source = 'appcoll_import',
                               effective_from = '2000-01-01'
                         where rule_id = %s and version = 1
                        """,
                        (m.name, m.trigger_code, m.jurisdiction_code, json.dumps(m.definition),
                         m.import_tags, existing[0]),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """
                        insert into app.docket_rules
                          (name, trigger_code, jurisdiction_code, definition, active,
                           approval_status, source, appcoll_task_type_id, import_tags, created_by,
                           effective_from)
                        values (%s, %s, %s, %s, false, %s, 'appcoll_import', %s, %s, %s,
                                '2000-01-01')
                        """,
                        (m.name, m.trigger_code, m.jurisdiction_code, json.dumps(m.definition),
                         approval, m.appcoll_task_type_id, m.import_tags,
                         identity.entra.os_user_id),
                    )
                    imported += 1
            for u in summary.unresolved:
                conn.execute(
                    """
                    insert into app.rule_import_unresolved (appcoll_task_type_id, reason, raw)
                    values (%s, %s, %s)
                    """,
                    (u.appcoll_task_type_id, u.reason, json.dumps(u.raw)),
                )
            for s in summary.ladder_stubs:
                conn.execute(
                    """
                    insert into app.a18_ladder_stubs (appcoll_task_type_id, reminder_of, raw)
                    values (%s, %s, %s)
                    """,
                    (s.appcoll_task_type_id, s.reminder_of, json.dumps(s.raw)),
                )
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc

    counts = summary.deadline_type_counts()
    return ImportOut(
        imported=imported, updated=updated, unresolved=len(summary.unresolved),
        ladder_stubs=len(summary.ladder_stubs), deadline_type_counts=counts,
        superseded_by_a1=summary.as_json()["superseded_by_a1"], total_rows=len(rows),
    )


@router.post("/{rule_id}/simulate")
def simulate(rule_id: UUID, body: SimulateRequest, identity: Identity) -> dict[str, Any]:
    """Dry-run the rule against a matter (M1-R4): preview generated dates + holiday-roll trace,
    persisting nothing. Runs even for draft/inactive rules — that is the whole point of previewing
    before activation."""
    with identity.connection() as conn:
        rule = conn.execute(
            "select definition, trigger_code, jurisdiction_code from app.docket_rules "
            "where rule_id = %s order by version desc limit 1",
            (rule_id,),
        ).fetchone()
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        try:
            preview = dry_run(conn, rule[0], body.matter_id, body.ref_date)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Matter not found") from exc
    preview["summary"] = _summ(rule[1], rule[2], rule[0])
    return preview


@router.post("/{rule_id}/approve", response_model=RuleOut)
def approve(rule_id: UUID, identity: Identity) -> RuleOut:
    """Activate the latest version of a draft rule (approval gate, M1-R4). Admin-only via RLS."""
    try:
        with identity.connection() as conn:
            row = conn.execute(
                """
                update app.docket_rules set approval_status = 'approved', active = true
                 where rule_id = %s
                   and version = (select max(version) from app.docket_rules where rule_id = %s)
                returning rule_id, version, name, trigger_code, jurisdiction_code, definition,
                          active, approval_status::text, source::text, appcoll_task_type_id,
                          import_tags
                """,
                (rule_id, rule_id),
            ).fetchone()
            if row is None:
                # The UPDATE matched no rows. Distinguish "rule doesn't exist" (404) from
                # "exists but the RLS write policy filtered it out for this caller" (403) — the
                # docket_rules write policy USING clause silently excludes rows for non-admins,
                # so an UPDATE never raises InsufficientPrivilege the way an INSERT would.
                visible = conn.execute(
                    "select 1 from app.docket_rules where rule_id = %s limit 1", (rule_id,)
                ).fetchone()
                if visible is not None:
                    raise HTTPException(status_code=403, detail="Not permitted to approve rules")
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return RuleOut(
        rule_id=row[0], version=row[1], name=row[2], trigger_code=row[3], jurisdiction_code=row[4],
        definition=row[5], active=row[6], approval_status=row[7], source=row[8],
        appcoll_task_type_id=row[9], import_tags=row[10], summary=_summ(row[3], row[4], row[5]),
    )


@router.post("/{rule_id}/versions", response_model=RuleOut, status_code=201)
def new_version(rule_id: UUID, body: EditRequest, identity: Identity) -> RuleOut:
    """Edit a rule = create a new version (append-only, M1-R4). The new version lands draft/
    inactive; in-flight tasks keep their generating version. Requires a structurally valid
    definition."""
    err = rule_definition_is_valid(body.definition)
    if err:
        raise HTTPException(status_code=422, detail=err)
    try:
        with identity.connection() as conn:
            base = conn.execute(
                "select trigger_code, jurisdiction_code, appcoll_task_type_id, import_tags "
                "from app.docket_rules where rule_id = %s order by version desc limit 1",
                (rule_id,),
            ).fetchone()
            if base is None:
                raise HTTPException(status_code=404, detail="Rule not found")
            # Carry the AppColl lineage id + tags onto every version so the rule stays findable by
            # its origin id after edits (the v1-only unique index still allows this).
            row = conn.execute(
                """
                insert into app.docket_rules
                  (rule_id, version, name, trigger_code, jurisdiction_code, definition, active,
                   approval_status, source, created_by, appcoll_task_type_id, import_tags,
                   effective_from)
                values (%s,
                        (select max(version) + 1 from app.docket_rules where rule_id = %s),
                        %s, %s, %s, %s, false, 'draft', 'ui_edit', %s, %s, %s, current_date)
                returning rule_id, version, name, trigger_code, jurisdiction_code, definition,
                          active, approval_status::text, source::text, appcoll_task_type_id,
                          import_tags
                """,
                (rule_id, rule_id, body.definition["title"], base[0], base[1],
                 json.dumps(body.definition), identity.entra.os_user_id, base[2], base[3]),
            ).fetchone()
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    if row is None:  # insert...returning always yields a row on success; guard for the type checker
        raise HTTPException(status_code=500, detail="Rule version insert returned no row")
    return RuleOut(
        rule_id=row[0], version=row[1], name=row[2], trigger_code=row[3], jurisdiction_code=row[4],
        definition=row[5], active=row[6], approval_status=row[7], source=row[8],
        appcoll_task_type_id=row[9], import_tags=row[10], summary=_summ(row[3], row[4], row[5]),
    )
