"""Knowledge Base retrieval against Postgres (WP 6.8, §12.1).

Exercises the parts that only exist in the database: FTS ranking and weighting, edition
supersession, the ingestion idempotence that makes a scheduled refresh viable, and the
admin-gated curation policy.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared.config import settings
from py_shared.domain import knowledge_base as kb

ADMIN_ID = "11111111-1111-1111-1111-111111111111"   # Principal — permissions admin
STAFF_ID = "22222222-2222-2222-2222-222222222222"   # Agent — no admin domains
ADMIN = f"dev:{ADMIN_ID}:dev.user@brunetco.com"
STAFF = f"dev:{STAFF_ID}:dev.agent@brunetco.com"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('kb.chunks')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.8 migration (0017) not applied")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@pytest.fixture()
def source(su: psycopg.Connection) -> Iterator[str]:
    """An isolated open-licence source, removed afterwards so FTS tests don't see each other."""
    key = f"test-{uuid.uuid4().hex[:8]}"
    row = su.execute(
        "insert into kb.sources (key, name, jurisdiction, authority_type, license_class, "
        "edition_label) values (%s, 'Test Manual', 'CA', 'manual', 'open', 'ed-1') returning id",
        (key,),
    ).fetchone()
    assert row is not None
    yield str(row[0])
    su.execute("delete from kb.sources where key = %s", (key,))


SAMPLE = """17.02 Double patenting
A claim is not patentable if it is not patentably distinct from a claim in a
co-pending application owned by the same applicant.

17.03 Unity of invention
An application shall relate to one invention only.
"""


# --- ingestion -----------------------------------------------------------------


def test_ingestion_writes_citation_carrying_chunks(su: psycopg.Connection, source: str) -> None:
    doc_id, written = kb.ingest_document(
        su, uuid.UUID(source), "Chapter 17", SAMPLE, "MOPOP"
    )
    assert doc_id is not None and written > 0
    rows = su.execute(
        "select citation from kb.chunks where source_id = %s", (source,)
    ).fetchall()
    assert "MOPOP §17.02" in {r[0] for r in rows}


def test_re_ingesting_unchanged_content_is_a_no_op(
    su: psycopg.Connection, source: str,
) -> None:
    """What makes a scheduled full-corpus refresh cheap and safe: unchanged documents skip
    re-chunking, so chunk ids stay stable and citations held elsewhere are not orphaned."""
    first_id, first_count = kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    second_id, second_count = kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    assert second_id == first_id
    assert first_count > 0 and second_count == 0


def test_changed_content_creates_a_new_document(su: psycopg.Connection, source: str) -> None:
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    new_id, count = kb.ingest_document(
        su, uuid.UUID(source), "Ch 17", SAMPLE + "\n17.04 New section\nAdded text.\n", "MOPOP"
    )
    assert new_id is not None and count > 0


# --- retrieval -----------------------------------------------------------------


def test_search_finds_a_passage_and_returns_its_citation(
    su: psycopg.Connection, source: str,
) -> None:
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    results = kb.search(su, "patentably distinct")
    assert results
    assert any(p.citation == "MOPOP §17.02" for p in results)


def test_heading_matches_outrank_passing_body_mentions(
    su: psycopg.Connection, source: str,
) -> None:
    """The tsvector weights citation and heading above body, so searching a topic returns the
    section that IS about it, not a paragraph that mentions it in passing."""
    text = (
        "17.02 Unity of invention\nThe requirement is set out here.\n\n"
        "17.09 Miscellaneous\nSee also the discussion of unity of invention elsewhere, "
        "which is not the operative provision.\n"
    )
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", text, "MOPOP")
    results = kb.search(su, "unity of invention")
    assert results[0].citation == "MOPOP §17.02"


def test_jurisdiction_filter_narrows_results(su: psycopg.Connection, source: str) -> None:
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    assert kb.search(su, "patentably distinct", jurisdictions=["CA"])
    assert kb.search(su, "patentably distinct", jurisdictions=["US"]) == []


def test_empty_query_returns_nothing_rather_than_everything(su: psycopg.Connection) -> None:
    assert kb.search(su, "   ") == []


# --- supersession --------------------------------------------------------------


def test_superseded_editions_are_excluded_by_default(
    su: psycopg.Connection, source: str,
) -> None:
    """An agent asking a plain question gets current law and must opt in to history."""
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    key = su.execute("select key from kb.sources where id = %s", (source,)).fetchone()
    assert key is not None
    kb.supersede_edition(su, key[0], date(2026, 7, 18))
    assert kb.search(su, "patentably distinct") == []


def test_superseded_content_is_retained_and_retrievable_on_request(
    su: psycopg.Connection, source: str,
) -> None:
    """Deleting superseded editions would destroy the firm's ability to explain advice it gave
    while that edition was in force."""
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    key = su.execute("select key from kb.sources where id = %s", (source,)).fetchone()
    assert key is not None
    kb.supersede_edition(su, key[0], date(2026, 7, 18))
    results = kb.search(su, "patentably distinct", include_superseded=True)
    assert results and all(p.is_superseded for p in results)


def test_grounding_snippets_never_include_superseded_practice(
    su: psycopg.Connection, source: str,
) -> None:
    """The A9 path: a drafted client letter must not rest on a revision that no longer applies."""
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    key = su.execute("select key from kb.sources where id = %s", (source,)).fetchone()
    assert key is not None
    kb.supersede_edition(su, key[0], date(2026, 7, 18))
    assert kb.grounding_snippets(su, "patentably distinct") == []


def test_a_new_edition_can_take_the_key_once_the_old_one_is_superseded(
    su: psycopg.Connection, source: str,
) -> None:
    row = su.execute("select key from kb.sources where id = %s", (source,)).fetchone()
    assert row is not None
    key = row[0]
    kb.supersede_edition(su, key, date(2026, 7, 18))
    su.execute(
        "insert into kb.sources (key, name, jurisdiction, authority_type, license_class, "
        "edition_label) values (%s, 'Test Manual', 'CA', 'manual', 'open', 'ed-2')",
        (key,),
    )
    su.execute("delete from kb.sources where key = %s", (key,))


def test_two_current_editions_of_one_source_are_refused(
    su: psycopg.Connection, source: str,
) -> None:
    """Ambiguity about which edition is in force is exactly what the freshness model exists to
    prevent, so the database refuses it."""
    row = su.execute("select key from kb.sources where id = %s", (source,)).fetchone()
    assert row is not None
    with pytest.raises(psycopg.errors.UniqueViolation):
        su.execute(
            "insert into kb.sources (key, name, jurisdiction, authority_type, license_class, "
            "edition_label) values (%s, 'Dup', 'CA', 'manual', 'open', 'ed-2')",
            (row[0],),
        )


# --- licence guard end to end --------------------------------------------------


def test_third_party_passages_come_back_truncated_from_the_database_path(
    su: psycopg.Connection,
) -> None:
    key = f"blog-{uuid.uuid4().hex[:8]}"
    row = su.execute(
        "insert into kb.sources (key, name, jurisdiction, authority_type, license_class, "
        "edition_label) values (%s, 'Curated Blog', 'CA', 'curated_blog', 'grounding_only', "
        "'live') returning id",
        (key,),
    ).fetchone()
    assert row is not None
    try:
        body = "Commentary on double patenting. " + ("filler text " * 300)
        kb.ingest_document(su, uuid.UUID(str(row[0])), "Post", body, "Blog")
        results = kb.search(su, "double patenting")
        blog = [p for p in results if p.source_key == key]
        assert blog
        assert not blog[0].may_quote
        assert len(blog[0].extract) <= kb.GROUNDING_ONLY_EXTRACT_CHARS + 6
    finally:
        su.execute("delete from kb.sources where key = %s", (key,))


# --- RLS + API -----------------------------------------------------------------


def test_all_staff_can_read_the_kb(su: psycopg.Connection, source: str) -> None:
    """The KB holds no client data — there is nothing here to scope per user."""
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    with _user_conn(STAFF_ID) as conn:
        assert kb.search(conn, "patentably distinct")


def test_curation_writes_are_admin_gated(su: psycopg.Connection) -> None:
    """A bad source silently corrupts the grounding under every agent, so a non-admin cannot
    register one."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), _user_conn(STAFF_ID) as conn:
        conn.execute(
            "insert into kb.sources (key, name, jurisdiction, authority_type, license_class, "
            "edition_label) values ('rogue', 'Rogue', 'CA', 'curated_blog', 'open', 'v1')"
        )


def test_search_endpoint_returns_citations_and_the_quote_flag(
    su: psycopg.Connection, source: str,
) -> None:
    kb.ingest_document(su, uuid.UUID(source), "Ch 17", SAMPLE, "MOPOP")
    response = client.get(
        "/api/v1/kb/search", params={"q": "patentably distinct"}, headers=_hdr(STAFF)
    )
    assert response.status_code == 200
    body = response.json()
    assert body and body[0]["citation"].startswith("MOPOP")
    assert body[0]["may_quote"] is True


def test_sources_endpoint_reports_freshness(su: psycopg.Connection) -> None:
    response = client.get("/api/v1/kb/sources", headers=_hdr(ADMIN))
    assert response.status_code == 200
    sources = {s["key"]: s for s in response.json()}
    # Seeded in migration 0017 and never ingested — must read as stale, not as fresh.
    assert sources["mopop"]["is_stale"] is True


def test_stale_only_filter_narrows_the_source_list(su: psycopg.Connection) -> None:
    response = client.get(
        "/api/v1/kb/sources", params={"stale_only": True}, headers=_hdr(ADMIN)
    )
    assert response.status_code == 200
    assert all(s["is_stale"] for s in response.json())
