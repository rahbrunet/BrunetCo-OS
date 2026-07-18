"""A9 pipeline + review queue against Postgres (WP 6.9) — per-user isolation proven at the DB.

The isolation test is the one that matters. A9 reads a user's mailbox and imitates their voice;
if another user can read the drafts or the style corpus, whole-firm mailbox ingestion (D15) stops
being compatible with mailbox privacy (D39). So it is asserted through RLS, not through a route
filter — a route filter is a line of code someone can delete.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from app.main import app
from fastapi.testclient import TestClient
from py_shared import llm, redaction
from py_shared.config import settings
from py_shared.domain import drafting

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
STAFF_ID = "22222222-2222-2222-2222-222222222222"
ADMIN = f"dev:{ADMIN_ID}:dev.user@brunetco.com"
STAFF = f"dev:{STAFF_ID}:dev.agent@brunetco.com"


def _ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.email_drafts')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="WP6.9 migration (0015) not applied")

client = TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeNer:
    @property
    def name(self) -> str:
        return "fake:test"

    def available(self) -> bool:
        return True

    def entities(self, text: str) -> list[tuple[str, str]]:
        return [(v, "PERSON") for v in ("Jane Smith",) if v in text]


class FakeLlm:
    """Echoes a fixed reply. `canned` lets a test drive the validator path."""

    def __init__(self, canned: str = "Hi [PERSON_1] — the report is due 2026-09-01.") -> None:
        self._canned = canned

    @property
    def provider(self) -> str:
        return llm.PROVIDER_BEDROCK

    @property
    def model(self) -> str:
        return "us.anthropic.claude-sonnet-4-6"

    def complete(self, prompt: str, system: str = "") -> str:
        # Guard the invariant at the boundary the model actually sees.
        assert "Jane Smith" not in prompt, "unredacted name reached the provider"
        return self._canned


def _request(author: str) -> drafting.DraftRequest:
    return drafting.DraftRequest(
        author_user_id=uuid.UUID(author),
        thread=[drafting.ThreadMessage("jane@acme.com", "2026-07-18",
                                       "Jane Smith here — any update on the report?")],
        subject="Status",
    )


@pytest.fixture()
def su() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        yield conn


@contextmanager
def _user_conn(user_id: str) -> Iterator[psycopg.Connection]:
    """RLS-scoped connection for a user, via the same D44 bridge the API uses.

    Must stay a context manager: `user_connection` sets the JWT claims with `set_config(..., true)`,
    which is transaction-local, so the identity only holds for the life of the `with` block.
    """
    from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection

    jwt = mint_supabase_jwt(EntraIdentity(os_user_id=user_id, email="t@brunetco.com"))
    with user_connection(jwt) as conn:
        yield conn


# --- pipeline ------------------------------------------------------------------


def test_draft_lands_in_the_queue_and_never_sends(su: psycopg.Connection) -> None:
    outcome = drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    assert outcome.status == "pending_review"
    assert outcome.draft_id is not None
    row = su.execute(
        "select status::text, body_text from app.email_drafts where id = %s", (outcome.draft_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "pending_review"
    # Rehydrated for the human even though the provider only ever saw the placeholder.
    assert "Jane Smith" in row[1]


def test_every_call_leaves_a_redaction_and_egress_audit_pair(su: psycopg.Connection) -> None:
    """D45 spine: the egress row's FK to the redaction row makes 'sent unredacted'
    unrepresentable."""
    outcome = drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    red = su.execute(
        "select leaks, entity_counts from ops.redaction_events where ref = %s",
        (outcome.redaction_ref,),
    ).fetchone()
    assert red is not None and red[0] == 0
    egress = su.execute(
        "select provider, status from ops.llm_egress_log where redaction_ref = %s",
        (outcome.redaction_ref,),
    ).fetchone()
    assert egress is not None
    assert egress[0] == "bedrock" and egress[1] == "sent"


def test_egress_log_cannot_reference_a_redaction_that_never_happened(
    su: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        su.execute(
            """
            insert into ops.llm_egress_log
              (agent_name, task, sensitivity, provider, model, redaction_ref)
            values ('a9-drafter', 'draft_reply', 'sensitive', 'bedrock', 'm', 'red_fabricated')
            """
        )


def test_credential_shaped_model_output_is_discarded_not_queued(su: psycopg.Connection) -> None:
    outcome = drafting.draft_reply(
        su, _request(ADMIN_ID), ner_backend=FakeNer(),
        client=FakeLlm("The password is hunter2, see AKIAIOSFODNN7EXAMPLE."),
    )
    assert outcome.status == "discarded"
    assert any(r.startswith("credential_shaped") for r in outcome.discard_reasons)
    body = su.execute(
        "select body_text from app.email_drafts where id = %s", (outcome.draft_id,)
    ).fetchone()
    # Discarded while still masked: a rejected draft never gets real names stitched back in.
    assert body is not None and body[0] == ""


def test_unavailable_ner_stops_the_draft_entirely(su: psycopg.Connection) -> None:
    class Dead(FakeNer):
        def available(self) -> bool:
            return False

    with pytest.raises(redaction.RedactionUnavailable):
        drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=Dead(), client=FakeLlm())


def test_agent_is_registered_without_any_send_action(su: psycopg.Connection) -> None:
    """The registry-level half of the never-send guarantee."""
    row = su.execute(
        "select allowed_actions from ops.agents where name = 'a9-drafter'"
    ).fetchone()
    assert row is not None
    assert "email.send" not in row[0]


# --- per-user isolation (RLS, direct Postgres) ---------------------------------


def test_a_user_cannot_read_another_users_drafts(su: psycopg.Connection) -> None:
    outcome = drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.email_drafts where id = %s", (outcome.draft_id,)
        ).fetchone()
        assert row is not None and row[0] == 0


def test_a_user_cannot_read_another_users_style_corpus(su: psycopg.Connection) -> None:
    """The corpus is the user's own sent mail. An admin override here would make every mailbox
    readable by proxy, so there deliberately isn't one."""
    su.execute(
        "insert into app.draft_style_examples (user_id, body_text) values (%s, %s)",
        (ADMIN_ID, "Kind regards, as always."),
    )
    with _user_conn(STAFF_ID) as conn:
        row = conn.execute(
            "select count(*) from app.draft_style_examples where user_id = %s", (ADMIN_ID,)
        ).fetchone()
        assert row is not None and row[0] == 0


def test_style_examples_are_selected_for_the_calling_user_only(su: psycopg.Connection) -> None:
    su.execute(
        "insert into app.draft_style_examples (user_id, body_text) values (%s, %s)",
        (STAFF_ID, "Thanks — I'll revert shortly."),
    )
    with _user_conn(STAFF_ID) as conn:
        examples = drafting.select_style_examples(conn, uuid.UUID(STAFF_ID))
        assert any("revert shortly" in e for e in examples)


# --- API -----------------------------------------------------------------------


def test_queue_lists_only_the_callers_drafts(su: psycopg.Connection) -> None:
    mine = drafting.draft_reply(su, _request(STAFF_ID), ner_backend=FakeNer(), client=FakeLlm())
    theirs = drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=FakeNer(), client=FakeLlm())

    response = client.get("/api/v1/drafts", headers=_hdr(STAFF))
    assert response.status_code == 200
    ids = {d["id"] for d in response.json()}
    assert str(mine.draft_id) in ids
    assert str(theirs.draft_id) not in ids


def test_approving_marks_ready_without_sending(su: psycopg.Connection) -> None:
    outcome = drafting.draft_reply(su, _request(STAFF_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    response = client.post(
        f"/api/v1/drafts/{outcome.draft_id}/decision", json={"approve": True}, headers=_hdr(STAFF)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_deciding_someone_elses_draft_is_indistinguishable_from_absent(
    su: psycopg.Connection,
) -> None:
    """404 either way, so the endpoint cannot be used to probe for other users' drafts."""
    outcome = drafting.draft_reply(su, _request(ADMIN_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    response = client.post(
        f"/api/v1/drafts/{outcome.draft_id}/decision", json={"approve": True}, headers=_hdr(STAFF)
    )
    assert response.status_code == 404


def test_a_decided_draft_cannot_be_decided_again(su: psycopg.Connection) -> None:
    outcome = drafting.draft_reply(su, _request(STAFF_ID), ner_backend=FakeNer(),
                                   client=FakeLlm())
    url = f"/api/v1/drafts/{outcome.draft_id}/decision"
    assert client.post(url, json={"approve": False}, headers=_hdr(STAFF)).status_code == 200
    assert client.post(url, json={"approve": True}, headers=_hdr(STAFF)).status_code == 404
