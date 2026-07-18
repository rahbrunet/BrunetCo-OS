"""Watcher observability endpoints (WP 6.2) — failure tags on the matter + the run log.

Failures are RLS-scoped (a user only sees failures on matters they can see); the run log is
firm-general ops data feeding the dashboard. All reads run on the caller's connection (D44).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/watchers", tags=["watchers"])


class WatcherFailureOut(BaseModel):
    id: UUID
    matter_id: UUID
    run_id: UUID | None
    tag: str
    detail: str | None
    occurred_at: datetime


class WatcherRunOut(BaseModel):
    id: UUID
    agent_name: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    stats: dict[str, Any]


@router.get("/failures", response_model=list[WatcherFailureOut])
def list_failures(
    identity: Identity, matter_id: UUID | None = None, tag: str | None = None,
) -> list[WatcherFailureOut]:
    """Failure tags, queryable per matter or per tag (CIPO 500 / scrape error / no data /
    download failed) — the legacy Error column, now visible on the matter."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, matter_id, run_id, tag::text, detail, occurred_at
              from app.watcher_failures
             where (%(matter_id)s::uuid is null or matter_id = %(matter_id)s)
               and (%(tag)s::text is null or tag::text = %(tag)s)
             order by occurred_at desc
             limit 500
            """,
            {"matter_id": matter_id, "tag": tag},
        ).fetchall()
    return [
        WatcherFailureOut(id=r[0], matter_id=r[1], run_id=r[2], tag=r[3], detail=r[4],
                          occurred_at=r[5])
        for r in rows
    ]


@router.get("/runs", response_model=list[WatcherRunOut])
def list_runs(identity: Identity, agent_name: str | None = None) -> list[WatcherRunOut]:
    """Per-run summaries (rows / new / handled / downloaded / errors) for the ops dashboard."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, agent_name, started_at, finished_at, status, stats
              from ops.watcher_runs
             where (%(agent)s::text is null or agent_name = %(agent)s)
             order by started_at desc
             limit 100
            """,
            {"agent": agent_name},
        ).fetchall()
    return [
        WatcherRunOut(id=r[0], agent_name=r[1], started_at=r[2], finished_at=r[3], status=r[4],
                      stats=r[5])
        for r in rows
    ]
