"""A9 email drafter (WP 6.9 §2–§3, spec §A9) — reply drafting into a private review queue.

The pipeline, ported from the legacy single-inbox service and generalized per-user:

    assemble  ->  redact  ->  egress gate  ->  LLM  ->  validate  ->  rehydrate  ->  review queue

Every arrow is load-bearing:

  * `redact` before the gate, because the gate's whole contract is "no redaction ref, no call".
  * `validate` before `rehydrate`, so a model response that echoed a credential or an injected
    instruction is discarded while it is still de-identified — a discarded draft never gets real
    names stitched back into it.
  * `review queue` and nothing after it. A9 drafts; a human sends. The agent's orchestrator
    registration omits `email.send` entirely (migration 0015), so the never-send guarantee holds
    at the registry level even if this module were rewritten badly.

Per-user, not per-firm (§3): each user's drafts are written from their own mailbox and grounded
in their own sent mail, and RLS scopes both to them (D39). The legacy service watched one inbox
under delegated auth that needed a manually unlocked vault; the OS target is app-only Graph so
drafting survives a reboot without a human at the keyboard — that auth work belongs to WP 4.3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from py_shared import llm, redaction
from py_shared.orchestrator import LLM_EGRESS, egress_check

AGENT_NAME = "a9-drafter"
TASK = "draft_reply"

# How many style examples ground one draft. The legacy service used the top 7 semantically
# similar sent messages; the ranking is pluggable (see `select_style_examples`) but the count is
# kept — more examples crowded out the thread itself in the context window.
STYLE_EXAMPLE_COUNT = 7


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreadMessage:
    sender: str
    sent_at: str
    body: str


@dataclass(frozen=True)
class Attachment:
    """Extracted text of a PDF/docx attachment. Extraction itself lives in the ingestion
    pipeline (WP 4.3) — this module consumes text, never bytes."""

    filename: str
    text: str


@dataclass
class DraftRequest:
    author_user_id: UUID
    thread: list[ThreadMessage]
    attachments: list[Attachment] = field(default_factory=list)
    style_examples: list[str] = field(default_factory=list)
    # Client/party names pulled from the matter — masked unconditionally rather than left to the
    # model to notice (see redaction.redact).
    known_entities: list[str] = field(default_factory=list)
    # Firm principals: left unmasked so the drafter can reproduce a real signature and voice.
    allow_entities: list[str] = field(default_factory=list)
    matter_id: UUID | None = None
    in_reply_to: UUID | None = None
    subject: str | None = None
    # KB citations (WP 6.8) are grounded substance, distinct from style. Empty until 6.8 ships.
    kb_snippets: list[str] = field(default_factory=list)


SYSTEM_PROMPT = (
    "You are drafting a reply on behalf of a Canadian intellectual property practitioner. "
    "Match the voice of the STYLE EXAMPLES: their greeting, register, sentence length and "
    "sign-off. Use the KNOWLEDGE BASE excerpts for any statement of law or procedure, and cite "
    "the section you relied on. Personal names, organizations and contact details have been "
    "replaced with placeholders such as PERSON_001 and ORG_002; reproduce those placeholders "
    "exactly as given and never invent new ones. Reply with the body of the email only."
)


def assemble_prompt(request: DraftRequest) -> str:
    """Build the user prompt: style, then grounded substance, then attachments, then the thread.

    Thread last, and newest message last within it, because that is the question being answered —
    the model should read everything else as context leading up to it.
    """
    parts: list[str] = []

    if request.style_examples:
        parts.append(
            "STYLE EXAMPLES (this user's own previous replies — imitate the voice, not the "
            "content):\n"
            + "\n---\n".join(request.style_examples)
        )
    if request.kb_snippets:
        parts.append(
            "KNOWLEDGE BASE (authoritative; cite what you use):\n"
            + "\n---\n".join(request.kb_snippets)
        )
    if request.attachments:
        parts.append(
            "ATTACHMENTS:\n"
            + "\n---\n".join(f"[{a.filename}]\n{a.text}" for a in request.attachments)
        )

    thread = "\n\n".join(
        f"From: {m.sender}\nDate: {m.sent_at}\n{m.body}" for m in request.thread
    )
    parts.append(f"EMAIL THREAD (oldest first; reply to the last message):\n{thread}")
    parts.append("Draft the reply body.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Response validation (legacy response_validator.py)
# ---------------------------------------------------------------------------

# Shapes that must never reach a review queue, let alone a client. Two families:
# credential-shaped output (the model regurgitating a secret that reached it some other way) and
# injection-shaped output (the model obeying an instruction embedded in an inbound email, which
# for a drafter is the realistic attack — anyone can email the firm).
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\b(?:sk|pk|api[_-]?key|secret)[-_][A-Za-z0-9]{12,}", re.I)),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}", re.I)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("password_disclosure", re.compile(r"\bpassword\s*(?:is|:)\s*\S+", re.I)),
)

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction_override", re.compile(
        r"\bignore (?:all )?(?:your |the )?previous instructions\b", re.I)),
    ("role_override", re.compile(
        r"\byou are now\b.{0,40}\b(?:admin|developer|unrestricted)\b", re.I)),
    ("exfiltration", re.compile(r"\b(?:send|forward|email)\b.{0,30}\b(?:to)\b\s*\S+@\S+", re.I)),
    ("system_prompt_leak", re.compile(r"\b(?:system prompt|my instructions are)\b", re.I)),
)


def validate_response(text: str, mapping: dict[str, str]) -> list[str]:
    """Reasons to discard the model's response; empty means the draft may proceed.

    Runs on the *masked* response, before rehydration. Also flags placeholders the model invented
    ("[PERSON_9]" when only three were issued): a fabricated placeholder rehydrates to nothing and
    would ship a literal bracket-token to a client, and it means the model was improvising about
    identity — reason enough not to trust the rest of the draft.
    """
    reasons: list[str] = []
    for name, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            reasons.append(f"credential_shaped:{name}")
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            reasons.append(f"injection_shaped:{name}")
    unknown = {
        ph for ph in re.findall(r"\b[A-Z]{3,}_\d{3,}\b", text) if ph not in mapping
    }
    if unknown:
        reasons.append(f"unknown_placeholder:{','.join(sorted(unknown))}")
    if not text.strip():
        reasons.append("empty_response")
    return reasons


# ---------------------------------------------------------------------------
# Style corpus (per-user)
# ---------------------------------------------------------------------------


def select_style_examples(
    conn: psycopg.Connection, user_id: UUID, limit: int = STYLE_EXAMPLE_COUNT,
) -> list[str]:
    """This user's most recent sent messages, as style grounding.

    Recency-ranked. The legacy service ranked by ChromaDB semantic similarity to the inbound
    thread, which is better and belongs here once the OS has an embedding store — the interface
    is deliberately "give me N examples" so that swap touches this function alone. RLS already
    confines the rows to the calling user; the explicit `user_id` filter documents the intent and
    keeps the query honest if it is ever run on a system connection.
    """
    rows = conn.execute(
        """
        select body_text from app.draft_style_examples
         where user_id = %s
         order by coalesce(sent_at, created_at) desc
         limit %s
        """,
        (user_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class DraftOutcome:
    draft_id: UUID | None
    status: str
    redaction_ref: str
    provider: str
    model: str
    discard_reasons: list[str] = field(default_factory=list)


def draft_reply(
    conn: psycopg.Connection,
    request: DraftRequest,
    ner_backend: redaction.NerBackend | None = None,
    client: llm.LlmClient | None = None,
) -> DraftOutcome:
    """Run one draft end-to-end and land it in the author's review queue.

    Raises `RedactionUnavailable`/`RedactionLeak` rather than degrading: a drafter that cannot
    de-identify is a drafter that does not run. `LlmError` propagates for the same reason — the
    caller must distinguish "no draft" from "empty draft", and silently queueing an empty draft
    would look like the model had nothing to say.
    """
    prompt = assemble_prompt(request)

    # 1. Redact + audit. Fails closed; returns the ref the gate demands.
    result = redaction.redact_for_egress(
        conn, AGENT_NAME, prompt, backend=ner_backend,
        known_entities=request.known_entities, allow_entities=request.allow_entities,
    )

    # 2. Gate. Belt and braces: `redact_for_egress` cannot return without a ref, and the gate
    #    refuses without one — so the two controls fail independently.
    egress_check(LLM_EGRESS, result.ref)

    # 3. Provider call on the masked prompt only.
    client = client or llm.get_llm_client(TASK)
    try:
        raw = client.complete(result.masked, system=SYSTEM_PROMPT)
    except llm.LlmError as exc:
        llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked),
                       status="failed", detail=str(exc)[:500])
        raise
    llm.log_egress(conn, AGENT_NAME, TASK, client, result.ref, len(result.masked))

    # 4. Validate while still masked.
    reasons = validate_response(raw, result.mapping)
    if reasons:
        draft_id = _insert_draft(
            conn, request, body="", status="discarded", client=client,
            redaction_ref=result.ref, discard_reasons=reasons,
        )
        return DraftOutcome(draft_id, "discarded", result.ref, client.provider, client.model,
                            reasons)

    # 5. Rehydrate and queue for the human.
    body = redaction.rehydrate(raw, result.mapping)
    draft_id = _insert_draft(
        conn, request, body=body, status="pending_review", client=client,
        redaction_ref=result.ref,
    )
    return DraftOutcome(draft_id, "pending_review", result.ref, client.provider, client.model)


def _insert_draft(
    conn: psycopg.Connection,
    request: DraftRequest,
    body: str,
    status: str,
    client: llm.LlmClient,
    redaction_ref: str,
    discard_reasons: list[str] | None = None,
) -> UUID:
    row = conn.execute(
        """
        insert into app.email_drafts
          (author_user_id, in_reply_to, matter_id, subject, body_text, status, provider, model,
           redaction_ref, discard_reasons)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (request.author_user_id, request.in_reply_to, request.matter_id, request.subject, body,
         status, client.provider, client.model, redaction_ref, discard_reasons),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def decide_draft(
    conn: psycopg.Connection, draft_id: UUID, approve: bool, decided_by: UUID,
) -> str:
    """Accept or decline a draft. Approval marks it ready — it does not send.

    Sending is a separate, human-initiated action (WP 4.5). Collapsing the two would turn one
    click into an irreversible outbound message, which is precisely the design A9 avoids.
    """
    status = "approved" if approve else "rejected"
    row = conn.execute(
        """
        update app.email_drafts
           set status = %s::app.email_draft_status, decided_at = now(), decided_by = %s
         where id = %s and status = 'pending_review'
         returning status::text
        """,
        (status, decided_by, draft_id),
    ).fetchone()
    if row is None:
        raise LookupError("draft not found, not visible, or already decided")
    return str(row[0])
