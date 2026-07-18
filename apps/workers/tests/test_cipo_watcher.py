"""WP 6.2 CIPO watcher — golden tests against live Postgres with an injected fake scraper.

Covers the acceptance list in WP6.2-cipo-watcher-port-prompt.md:
  * presence-diff correctness (same-day multiple docs, back-dated doc, no-change, bootstrap);
  * CIPO 500 / scrape error / no-data → distinct queryable failure tags, not silent drops;
  * new doc → exactly one task + one M1-R14 provenance record + one M5 document record,
    idempotent on re-run;
  * client_abandoned still monitored, abandoned skipped, handled-by-others suppressed but
    recorded;
  * throttle respected (no request burst faster than the configured floor).
"""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from py_shared.config import settings
from py_shared.domain.watchers import (
    DocRow,
    doc_key,
    load_seen,
    select_new_docs,
)
from worker_app.cipo_watcher import ScrapeError, run_cipo_watcher


def _db_reachable() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.watcher_seen_docs')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="WP6.2 migration (0014) not applied"
)


# --- pure presence-diff (ports the legacy scheduler semantics) -----------------

LAST_RUN = date(2026, 7, 1)


def test_presence_diff_same_day_and_backdated() -> None:
    """Docs are new iff absent from the seen-set — the date plays no role once state exists."""
    seen = {doc_key(DocRow("Examiner's Report", "2026-06-01"))}
    docs = [
        DocRow("Response to Report", "2026-07-01"),   # same day as last run — still caught
        DocRow("Registration Notice", "2026-05-15"),  # back-dated — still caught
        DocRow("Examiner's Report", "2026-06-01"),    # already seen
    ]
    assert select_new_docs(docs, seen, LAST_RUN) == [0, 1]


def test_presence_diff_no_change() -> None:
    docs = [DocRow("Examiner's Report", "2026-06-01")]
    seen = {doc_key(docs[0])}
    assert select_new_docs(docs, seen, LAST_RUN) == []


def test_presence_diff_bootstrap_uses_date_cutoff() -> None:
    """No prior state → >= date cutoff (with grace), then the caller seeds the baseline."""
    docs = [
        DocRow("New Filing Certificate", "2026-07-01"),  # ON last_run day — kept (>=, not >)
        DocRow("Old Office Letter", "2026-06-20"),
    ]
    assert select_new_docs(docs, set(), LAST_RUN) == [0]
    # 15 days of grace sweeps the older doc in too.
    assert select_new_docs(docs, set(), LAST_RUN, bootstrap_grace_days=15) == [0, 1]


def test_doc_key_normalizes_whitespace_and_case() -> None:
    a = DocRow("Examiner's  Report", "2026-06-01")
    b = DocRow("examiner's report ", "2026-06-01")
    assert doc_key(a) == doc_key(b)


# --- DB-backed loop with a fake scraper ----------------------------------------

class FakeScraper:
    """Scripted per-application behaviour: list of DocRow = success; ScrapeError = raise."""

    def __init__(self, results: dict[str, object], download_dir: Path):
        self.results = results
        self.download_dir = download_dir
        self.search_calls: list[str] = []
        self.download_calls: list[tuple[str, list[int] | None]] = []

    def search_application(self, app_num: str) -> list[DocRow]:
        self.search_calls.append(app_num)
        result = self.results[app_num]
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, list)
        return result

    def download_documents(
        self, app_num: str, indices: list[int] | None = None, dest_dir: Path | None = None,
    ) -> Path:
        self.download_calls.append((app_num, indices))
        out = (dest_dir or self.download_dir) / f"PDF_{app_num}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"%PDF-1.4 fake " + app_num.encode())
        return out


def _mk_matter(
    conn: psycopg.Connection, suffix: str, app_no: str, status: str = "pending",
    handled: bool = False, jurisdiction: str = "CA",
) -> tuple[uuid.UUID, uuid.UUID]:
    client_id = conn.execute(
        "insert into app.clients (code, name) values (%s, 'Watch Co') returning id",
        (f"W{uuid.uuid4().hex[:5].upper()}",),
    ).fetchone()[0]
    family_id = conn.execute(
        "insert into app.families (client_id, family_seq, reference, title, family_type) "
        "values (%s, '0001', %s, 'Widget', 'patent') returning id",
        (client_id, f"W-{suffix}"),
    ).fetchone()[0]
    matter_id = conn.execute(
        """
        insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment,
                                 status, application_no, handled_by_others)
        values (%s, %s, %s, 'CA', %s, %s, %s) returning id
        """,
        (family_id, f"W-{suffix}-{jurisdiction}-{app_no}", jurisdiction, status, app_no, handled),
    ).fetchone()[0]
    return matter_id, family_id


def _run(conn: psycopg.Connection, scraper: FakeScraper, **kw: object):
    """Sweep only the scraper's scripted applications so parallel dev-DB data stays out."""
    kw.setdefault("application_nos", sorted(scraper.results.keys()))
    return run_cipo_watcher(conn, scraper, **kw)  # type: ignore[arg-type]


def test_new_doc_creates_task_provenance_document_idempotently(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex[:6]
    app_no = f"3{uuid.uuid4().int % 10**6:06d}"
    docs = [DocRow("Examiner's Report", "2026-07-10")]
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        matter_id, _family = _mk_matter(conn, suffix, app_no)
        # Prior seen-state so presence-diff (not bootstrap) drives the decision.
        conn.execute(
            "insert into app.watcher_seen_docs (matter_id, doc_key) values (%s, %s)",
            (matter_id, doc_key(DocRow("Filing Certificate", "2024-01-05"))),
        )
        conn.commit()

        scraper = FakeScraper({app_no: docs}, tmp_path)
        stats = _run(conn, scraper, download_dir=tmp_path)
        assert stats.new >= 1 and stats.tasks_created >= 1 and stats.downloaded >= 1

        tasks = conn.execute(
            "select id, title, deadline_type::text, ref_date, generated_by::text "
            "from app.tasks where matter_id = %s", (matter_id,),
        ).fetchall()
        assert len(tasks) == 1
        assert tasks[0][1] == "New CIPO document: Examiner's Report"
        assert tasks[0][2] == "event"
        assert tasks[0][3] == date(2026, 7, 10)
        assert tasks[0][4] == "agent"

        prov = conn.execute(
            "select trigger_type::text, source_ref from app.task_provenance "
            "where matter_id = %s", (matter_id,),
        ).fetchall()
        assert len(prov) == 1
        assert prov[0][0] == "watcher"
        assert prov[0][1] == f"cipo:{app_no}:{doc_key(docs[0])}"

        doc_rows = conn.execute(
            """
            select d.id from app.documents d
              join app.document_links l on l.document_id = d.id
             where l.matter_id = %s
            """,
            (matter_id,),
        ).fetchall()
        assert len(doc_rows) == 1

        # Baseline seeded → re-run detects nothing new, creates nothing (idempotent).
        stats2 = _run(conn, FakeScraper({app_no: docs}, tmp_path), download_dir=tmp_path)
        assert stats2.tasks_created == 0
        assert conn.execute(
            "select count(*) from app.tasks where matter_id = %s", (matter_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from app.task_provenance where matter_id = %s", (matter_id,)
        ).fetchone()[0] == 1

        # Cleanup (tests share the dev DB).
        conn.execute("delete from app.task_provenance where matter_id = %s", (matter_id,))
        conn.execute("delete from app.tasks where matter_id = %s", (matter_id,))
        conn.commit()


def test_failure_tags_cipo_500_scrape_error_no_data(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex[:6]
    apps = [f"4{uuid.uuid4().int % 10**6:06d}" for _ in range(3)]
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        m500, _ = _mk_matter(conn, f"{suffix}a", apps[0])
        merr, _ = _mk_matter(conn, f"{suffix}b", apps[1])
        mempty, _ = _mk_matter(conn, f"{suffix}c", apps[2])
        conn.commit()

        scraper = FakeScraper(
            {
                apps[0]: ScrapeError("CIPO server error 500 (record unavailable)",
                                     cipo_500=True),
                apps[1]: ScrapeError("locator timeout"),
                apps[2]: [],  # page loads but the docs table stays empty
            },
            tmp_path,
        )
        stats = _run(conn, scraper, download_dir=tmp_path)
        assert stats.errors >= 3

        tags = dict(conn.execute(
            "select matter_id::text, tag::text from app.watcher_failures "
            "where matter_id = any(%s)", ([m500, merr, mempty],),
        ).fetchall())
        assert tags[str(m500)] == "cipo_500"
        assert tags[str(merr)] == "scrape_error"
        assert tags[str(mempty)] == "no_data"

        # Retry budget respected: 3 search attempts per failing matter.
        assert scraper.search_calls.count(apps[0]) == 3
        assert scraper.search_calls.count(apps[2]) == 3


def test_status_scoping_and_handled_by_others(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex[:6]
    a_mon = f"5{uuid.uuid4().int % 10**6:06d}"   # client_abandoned — still monitored
    a_skip = f"6{uuid.uuid4().int % 10**6:06d}"  # abandoned — never scraped
    a_hbo = f"7{uuid.uuid4().int % 10**6:06d}"   # handled by others — recorded, no task
    docs = [DocRow("Maintenance Fee Notice", "2026-07-11")]
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        m_mon, _ = _mk_matter(conn, f"{suffix}m", a_mon, status="client_abandoned")
        _mk_matter(conn, f"{suffix}s", a_skip, status="abandoned")
        m_hbo, _ = _mk_matter(conn, f"{suffix}h", a_hbo, handled=True)
        for mid in (m_mon, m_hbo):
            conn.execute(
                "insert into app.watcher_seen_docs (matter_id, doc_key) values (%s, %s)",
                (mid, doc_key(DocRow("Filing Certificate", "2024-01-05"))),
            )
        conn.commit()

        scraper = FakeScraper({a_mon: docs, a_hbo: docs}, tmp_path)
        # a_skip is IN the sweep filter — its exclusion must come from the status rule alone.
        stats = _run(conn, scraper, download_dir=tmp_path,
                     application_nos=[a_mon, a_skip, a_hbo])

        assert a_skip not in scraper.search_calls          # abandoned skipped entirely
        assert a_mon in scraper.search_calls               # client_abandoned monitored
        assert stats.handled >= 1

        # client_abandoned got its task; handled-by-others got none but its baseline advanced.
        n_mon = conn.execute(
            "select count(*) from app.tasks where matter_id = %s", (m_mon,)
        ).fetchone()[0]
        n_hbo = conn.execute(
            "select count(*) from app.tasks where matter_id = %s", (m_hbo,)
        ).fetchone()[0]
        assert n_mon == 1 and n_hbo == 0
        assert doc_key(docs[0]) in load_seen(conn, m_hbo)  # recorded despite suppression
        assert (a_hbo, ) not in [(c[0],) for c in scraper.download_calls]

        conn.execute("delete from app.task_provenance where matter_id = %s", (m_mon,))
        conn.execute("delete from app.tasks where matter_id = %s", (m_mon,))
        conn.commit()


def test_download_failure_tagged_and_throttle_called(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex[:6]
    app_no = f"8{uuid.uuid4().int % 10**6:06d}"
    docs = [DocRow("Office Letter", "2026-07-12")]

    class FailingDownloadScraper(FakeScraper):
        def download_documents(
            self, app_num: str, indices: list[int] | None = None,
            dest_dir: Path | None = None,
        ) -> Path:
            self.download_calls.append((app_num, indices))
            raise RuntimeError("download modal never appeared")

    sleeps: list[bool] = []
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        matter_id, _ = _mk_matter(conn, suffix, app_no)
        conn.execute(
            "insert into app.watcher_seen_docs (matter_id, doc_key) values (%s, %s)",
            (matter_id, doc_key(DocRow("Filing Certificate", "2024-01-05"))),
        )
        conn.commit()

        scraper = FailingDownloadScraper({app_no: docs}, tmp_path)
        stats = _run(conn, scraper, download_dir=tmp_path,
                     throttle=lambda: sleeps.append(True))

        assert stats.failure_tags.get("download_failed") == 1
        tag = conn.execute(
            "select tag::text from app.watcher_failures where matter_id = %s", (matter_id,)
        ).fetchone()[0]
        assert tag == "download_failed"
        # 3 download attempts, throttled between attempts and between matters — the loop never
        # runs faster than the injected throttle allows.
        assert len(scraper.download_calls) == 3
        assert len(sleeps) >= 3

        # Task still created (the doc IS new); the download failure is tagged, not silent.
        assert conn.execute(
            "select count(*) from app.tasks where matter_id = %s", (matter_id,)
        ).fetchone()[0] == 1

        conn.execute("delete from app.task_provenance where matter_id = %s", (matter_id,))
        conn.execute("delete from app.tasks where matter_id = %s", (matter_id,))
        conn.commit()


def test_run_log_written(tmp_path: Path) -> None:
    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        stats = _run(conn, FakeScraper({}, tmp_path), limit=0, download_dir=tmp_path)
        row = conn.execute(
            "select status, stats from ops.watcher_runs where id = %s", (stats.run_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1]["rows"] == stats.rows


def test_hash_dedup_reuses_document_record(tmp_path: Path) -> None:
    """The same PDF bytes filed on two matters → one document record, two links (M6-R8)."""
    suffix = uuid.uuid4().hex[:6]
    a1 = f"91{uuid.uuid4().int % 10**5:05d}"
    a2 = f"92{uuid.uuid4().int % 10**5:05d}"
    docs = [DocRow("Identical Notice", "2026-07-13")]

    class SameBytesScraper(FakeScraper):
        def download_documents(
            self, app_num: str, indices: list[int] | None = None,
            dest_dir: Path | None = None,
        ) -> Path:
            out = (dest_dir or self.download_dir) / f"PDF_{app_num}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"%PDF-1.4 identical bytes " + suffix.encode())
            return out

    with psycopg.connect(settings.supabase_db_url, autocommit=False) as conn:
        m1, _ = _mk_matter(conn, f"{suffix}x", a1)
        m2, _ = _mk_matter(conn, f"{suffix}y", a2)
        for mid in (m1, m2):
            conn.execute(
                "insert into app.watcher_seen_docs (matter_id, doc_key) values (%s, %s)",
                (mid, doc_key(DocRow("Filing Certificate", "2024-01-05"))),
            )
        conn.commit()

        _run(conn, SameBytesScraper({a1: docs, a2: docs}, tmp_path), download_dir=tmp_path)

        n_docs = conn.execute(
            """
            select count(distinct l.document_id) from app.document_links l
             where l.matter_id in (%s, %s)
            """,
            (m1, m2),
        ).fetchone()[0]
        n_links = conn.execute(
            "select count(*) from app.document_links where matter_id in (%s, %s)", (m1, m2),
        ).fetchone()[0]
        assert n_docs == 1 and n_links == 2

        for mid in (m1, m2):
            conn.execute("delete from app.task_provenance where matter_id = %s", (mid,))
            conn.execute("delete from app.tasks where matter_id = %s", (mid,))
        conn.commit()
