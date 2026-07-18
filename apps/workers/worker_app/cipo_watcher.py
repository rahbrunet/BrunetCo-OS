"""CIPO watcher run loop (WP 6.2, spec §A1) — OS-native plumbing around the ported scrape core.

Input: OS Matter records (jurisdiction CA, monitorable statuses) — the legacy AppColl-report-
email dependency is deleted, the OS is the system of record. Outputs: M5 document records,
M1 tasks + M1-R14 provenance, DB-backed presence-diff state, failure tags, run summary.

The scraper is injected behind a small protocol so the golden tests exercise every branch of
this loop against Postgres with a fake — the real Playwright engine (cipo_scraper.CipoScraper)
plugs in unchanged.

Idempotency: seen-state seeding, task creation and document capture commit together per matter
(the legacy save-every-25-rows checkpoint, made transactional). A crashed run re-scrapes at
most the in-flight matter; create_watcher_task's source_ref dedup prevents double-docketing.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg
from py_shared.domain.watchers import (
    DocRow,
    capture_document,
    create_watcher_task,
    doc_key,
    finish_run,
    last_completed_run_date,
    load_seen,
    monitorable_matters,
    record_failure,
    seed_seen,
    select_new_docs,
    start_run,
)
from py_shared.orchestrator import AgentDisabled, get_agent

log = logging.getLogger(__name__)

AGENT_NAME = "cipo-watcher"


class ScrapeError(Exception):
    """Raised by a scraper for any scrape failure. ``cipo_500`` marks the CIPO-server-cannot-
    render case (recheck later) as distinct from other scrape errors."""

    def __init__(self, message: str, cipo_500: bool = False):
        super().__init__(message)
        self.cipo_500 = cipo_500


class Scraper(Protocol):
    def search_application(self, app_num: str) -> list[DocRow]: ...

    def download_documents(
        self, app_num: str, indices: list[int] | None = None, dest_dir: Path | None = None,
    ) -> Path: ...


@dataclass
class RunStats:
    run_id: UUID | None = None
    rows: int = 0
    new: int = 0
    handled: int = 0
    downloaded: int = 0
    errors: int = 0
    tasks_created: int = 0
    failure_tags: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, int | dict[str, int]]:
        return {
            "rows": self.rows, "new": self.new, "handled": self.handled,
            "downloaded": self.downloaded, "errors": self.errors,
            "tasks_created": self.tasks_created, "failure_tags": self.failure_tags,
        }


def _classify_failure(err: Exception | None, attempts: int) -> tuple[str, str]:
    """Distinct failure tags per matter (legacy Error-column discipline)."""
    if err is not None and getattr(err, "cipo_500", False):
        return "cipo_500", "CIPO 500 (CIPO server cannot render record — recheck later)"
    if err is not None:
        return "scrape_error", f"SCRAPE ERROR after {attempts} tries: {err}"
    return "no_data", f"NO DATA (docs table empty after {attempts} tries)"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _doc_date(docs: list[DocRow], indices: list[int]) -> date | None:
    for i in indices:
        try:
            return datetime.strptime(docs[i].date_str.strip(), "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            continue
    return None


def run_cipo_watcher(
    conn: psycopg.Connection,
    scraper: Scraper,
    *,
    limit: int | None = None,
    application_nos: list[str] | None = None,
    download_dir: Path | None = None,
    bootstrap_grace_days: int = 0,
    max_search_attempts: int = 3,
    max_download_attempts: int = 3,
    throttle: Callable[[], None] | None = None,
) -> RunStats:
    """One watcher run over all monitorable CA matters. Commits per matter (checkpoint)."""
    agent = get_agent(conn, AGENT_NAME)
    if agent is None or not agent.enabled:
        raise AgentDisabled(f"agent {AGENT_NAME!r} is missing or disabled (kill switch)")

    sleep = throttle if throttle is not None else (lambda: None)
    dest = Path(download_dir) if download_dir else Path(tempfile.gettempdir()) / "cipo-watcher"

    # Bootstrap-only date cutoff (was last_run.txt): matters with no seen-state fall back to
    # this; everything else is pure presence-diff.
    last_run = last_completed_run_date(conn, AGENT_NAME) or (date.today() - timedelta(days=7))

    stats = RunStats()
    run_id = start_run(conn, AGENT_NAME)
    stats.run_id = run_id
    conn.commit()

    matters = monitorable_matters(conn, "CA", limit=limit, application_nos=application_nos)
    log.info("[cipo-watcher] run %s: %d monitorable matters (last_run=%s)",
             run_id, len(matters), last_run)

    try:
        for m in matters:
            stats.rows += 1
            docs: list[DocRow] | None = None
            last_err: Exception | None = None
            for attempt in range(1, max_search_attempts + 1):
                try:
                    docs = scraper.search_application(m.application_no)
                    last_err = None
                except Exception as e:  # noqa: BLE001 — every scrape failure becomes a tag
                    last_err = e
                    docs = None
                    log.warning("[cipo-watcher] scrape error for %s (attempt %d/%d): %s",
                                m.application_no, attempt, max_search_attempts, e)
                if docs:
                    break
                if attempt < max_search_attempts:
                    sleep()

            if not docs:
                tag, detail = _classify_failure(last_err, max_search_attempts)
                record_failure(conn, m.matter_id, run_id, tag, detail)
                stats.errors += 1
                stats.failure_tags[tag] = stats.failure_tags.get(tag, 0) + 1
                conn.commit()
                sleep()
                continue

            seen = load_seen(conn, m.matter_id)
            new_idx = select_new_docs(docs, seen, last_run, bootstrap_grace_days)

            if new_idx and m.handled_by_others:
                # Suppressed from task generation but still recorded (legacy #7): the baseline
                # advances so the docs are never re-flagged, and no task is created.
                stats.handled += 1
                log.info("[cipo-watcher] HANDLED BY OTHERS — %d new doc(s) for %s suppressed",
                         len(new_idx), m.application_no)
            elif new_idx:
                stats.new += 1

                created_tasks = 0
                for i in new_idx:
                    result = create_watcher_task(
                        conn, m.matter_id, m.family_id, m.application_no, docs[i], run_id,
                    )
                    if result is not None:
                        created_tasks += 1
                stats.tasks_created += created_tasks

                # All new docs download as ONE combined PDF (one CAPTCHA solve), filed as one
                # hash-deduplicated M5 document record.
                if created_tasks > 0:
                    pdf_err: Exception | None = None
                    for attempt in range(1, max_download_attempts + 1):
                        try:
                            # A failed download leaves the modal open / docs table gone, so
                            # re-run the search to restore the documents view first.
                            if attempt > 1:
                                scraper.search_application(m.application_no)
                            pdf_path = scraper.download_documents(
                                m.application_no, new_idx, dest_dir=dest,
                            )
                            capture_document(
                                conn, m.matter_id, pdf_path.name, _sha256(pdf_path),
                                _doc_date(docs, new_idx),
                            )
                            stats.downloaded += 1
                            pdf_err = None
                            break
                        except Exception as e:  # noqa: BLE001 — tag, don't crash the run
                            pdf_err = e
                            log.warning(
                                "[cipo-watcher] download failed for %s (attempt %d/%d): %s",
                                m.application_no, attempt, max_download_attempts, e)
                            if attempt < max_download_attempts:
                                sleep()
                    if pdf_err is not None:
                        detail = (f"DOWNLOAD FAILED after {max_download_attempts} tries: "
                                  f"{pdf_err}")
                        record_failure(conn, m.matter_id, run_id, "download_failed", detail)
                        stats.errors += 1
                        stats.failure_tags["download_failed"] = (
                            stats.failure_tags.get("download_failed", 0) + 1)

            # Seed/extend the baseline with everything currently on CIPO so today's docs are
            # not re-flagged next run — committed WITH the tasks (crash-safe checkpoint).
            seed_seen(conn, m.matter_id, {doc_key(d) for d in docs})
            conn.commit()
            sleep()
    except Exception:
        finish_run(conn, run_id, stats.as_json(), status="crashed")
        conn.commit()
        raise

    finish_run(conn, run_id, stats.as_json())
    conn.commit()
    log.info("[cipo-watcher] summary: rows=%d new=%d handled=%d downloaded=%d errors=%d",
             stats.rows, stats.new, stats.handled, stats.downloaded, stats.errors)
    return stats


def handle_cipo_run(payload: dict[str, object]) -> None:  # pragma: no cover — needs a browser
    """Event handler (`watcher.cipo_run`): build the real Playwright scraper with the 2Captcha
    key from the credential broker and run the watcher on a system connection."""
    from py_shared.config import settings
    from py_shared.orchestrator import fetch_secret

    from worker_app.cipo_scraper import CipoError, CipoScraper, CipoScraperConfig, make_throttle

    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        api_key = fetch_secret(conn, AGENT_NAME, "cipo/twocaptcha-api-key")
        config = CipoScraperConfig(twocaptcha_api_key=api_key)
        limit = payload.get("limit")
        with CipoScraper(config) as scraper:

            class _Adapter:
                """Maps the engine's CipoError(500) onto the loop's tagged ScrapeError."""

                def search_application(self, app_num: str) -> list[DocRow]:
                    try:
                        return scraper.search_application(app_num)
                    except CipoError as e:
                        raise ScrapeError(
                            str(e), cipo_500="server error 500" in str(e).lower()
                        ) from e

                def download_documents(
                    self, app_num: str, indices: list[int] | None = None,
                    dest_dir: Path | None = None,
                ) -> Path:
                    return scraper.download_documents(app_num, indices, dest_dir)

            run_cipo_watcher(
                conn, _Adapter(),
                limit=int(limit) if isinstance(limit, (int, str)) else None,
                download_dir=config.download_dir,
                throttle=make_throttle(config),
            )
