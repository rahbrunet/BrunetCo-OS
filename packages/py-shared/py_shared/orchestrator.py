"""Orchestrator (A0) core — the control plane agents act through (WP 6.1, spec §12).

Four concerns, kept small and testable:
  * registry     — an agent's enabled flag (kill switch) + what actions/secrets it may touch.
  * approval      — propose an action, then approve (= execute) or reject it. Approve dispatches
                    to a handler registry; a rejected/expired proposal never executes.
  * egress gate   — a single fail-closed check for data leaving the OS. LLM egress MUST carry a
                    redaction reference (D45): no redaction ref → refused, never sent unmasked.
  * credential broker — fetch a secret slot only if the agent is registered for it (deny + raise
                    otherwise), then resolve it through Bitwarden (``py_shared.secrets``) when
                    configured. This is the single choke point for every runtime secret.

Pure helpers (egress_check, broker_authorize) are unit-tested with no DB; the DB-touching pieces
run on a caller-supplied connection so the approval queue is RLS-scoped (D44) exactly like every
other user path.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

# Action-class taxonomy for the egress gate.
LLM_EGRESS = "llm"


class EgressRefused(RuntimeError):
    """The egress gate refused an outbound action (fail-closed)."""


class CredentialDenied(RuntimeError):
    """The credential broker refused a secret fetch for an unregistered slot."""


class AgentDisabled(RuntimeError):
    """The agent's kill switch is off; no action may be proposed or executed."""


# ---------------------------------------------------------------------------
# Egress gate (pure)
# ---------------------------------------------------------------------------

def egress_check(action_class: str, redaction_ref: str | None) -> None:
    """Fail-closed gate for outbound data (D8/D45). LLM egress must carry a redaction reference —
    the shared redaction service (WP 6.9) produces it. Anything else raises EgressRefused so a
    missing-redaction bug can suppress an LLM call but never leak an unmasked one."""
    if action_class == LLM_EGRESS and not redaction_ref:
        raise EgressRefused(
            "LLM egress requires a redaction reference (D45) — refusing to send unredacted data"
        )


# ---------------------------------------------------------------------------
# Credential broker (pure authorization; fetch is injected)
# ---------------------------------------------------------------------------

def broker_authorize(allowed_slots: list[str], slot: str) -> None:
    """Raise CredentialDenied unless ``slot`` is in the agent's registered allow-list."""
    if slot not in allowed_slots:
        raise CredentialDenied(f"agent not authorized for secret slot {slot!r}")


# ---------------------------------------------------------------------------
# Registry (DB)
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    name: str
    purpose: str
    enabled: bool
    allowed_actions: list[str]
    allowed_secret_slots: list[str]


def get_agent(conn: psycopg.Connection, name: str) -> Agent | None:
    row = conn.execute(
        "select name, purpose, enabled, allowed_actions, allowed_secret_slots "
        "from ops.agents where name = %s",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return Agent(name=row[0], purpose=row[1], enabled=row[2], allowed_actions=row[3],
                 allowed_secret_slots=row[4])


def fetch_secret(
    conn: psycopg.Connection, agent_name: str, slot: str,
    fetcher: Callable[[str], str] | None = None,
) -> str:
    """Broker a runtime secret fetch (D10). Denies slots outside the agent's allow-list; the actual
    value comes from ``fetcher`` — by default Bitwarden when BWS_ACCESS_TOKEN + BWS_PROJECT_ID are
    configured, otherwise a dev placeholder so the path is exercisable with no real credential.
    Never logs the value."""
    from py_shared.secrets import default_secret_fetcher

    agent = get_agent(conn, agent_name)
    if agent is None:
        raise CredentialDenied(f"unknown agent {agent_name!r}")
    broker_authorize(agent.allowed_secret_slots, slot)
    resolve = fetcher or default_secret_fetcher() or (lambda s: f"dev-secret::{s}")
    return resolve(slot)


# ---------------------------------------------------------------------------
# Approval queue (DB, RLS-scoped)
# ---------------------------------------------------------------------------

# Handlers that actually perform an approved action, keyed by action_type. WP 6.1 ships a demo
# handler; real ones (email.send via Graph, filing.stage, task.create) register at their WPs.
ActionHandler = Callable[[psycopg.Connection, dict[str, Any]], dict[str, Any]]


def _handle_demo_action(conn: psycopg.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    return {"handled": "demo.action", "echo": payload}


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "demo.action": _handle_demo_action,
}


@dataclass
class ProposedAction:
    id: UUID
    agent_name: str
    action_type: str
    status: str
    matter_id: UUID | None
    outcome: dict[str, Any] | None


def propose_action(
    conn: psycopg.Connection, agent_name: str, action_type: str,
    payload: dict[str, Any], matter_id: UUID | None = None,
    family_id: UUID | None = None, confidence: float | None = None,
) -> UUID:
    """Enqueue a proposed action for human approval. Enforces the kill switch and the agent's
    allowed-actions allow-list before anything reaches the queue."""
    agent = get_agent(conn, agent_name)
    if agent is None:
        raise CredentialDenied(f"unknown agent {agent_name!r}")
    if not agent.enabled:
        raise AgentDisabled(f"agent {agent_name!r} is disabled")
    if action_type not in agent.allowed_actions:
        raise CredentialDenied(f"agent {agent_name!r} not authorized for action {action_type!r}")
    row = conn.execute(
        """
        insert into app.proposed_actions
          (agent_name, action_type, matter_id, family_id, payload, confidence)
        values (%s, %s, %s, %s, %s, %s) returning id
        """,
        (agent_name, action_type, matter_id, family_id, json.dumps(payload), confidence),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def decide_action(
    conn: psycopg.Connection, action_id: UUID, approve: bool, decided_by: str,
) -> ProposedAction:
    """Approve (= execute) or reject a proposed action. On approve, the registered handler runs and
    its result is recorded; a handler error marks the action failed (not executed) with the error.
    Rejection never executes. Only a 'proposed' action can be decided (idempotent-safe)."""
    row = conn.execute(
        "select agent_name, action_type, matter_id, payload, status "
        "from app.proposed_actions where id = %s",
        (action_id,),
    ).fetchone()
    if row is None:
        raise LookupError("proposed action not found or not visible")
    agent_name, action_type, matter_id, payload, status = row
    if status != "proposed":
        raise ValueError(f"action already {status}")

    if not approve:
        conn.execute(
            "update app.proposed_actions set status='rejected', decided_by=%s, decided_at=now() "
            "where id=%s",
            (decided_by, action_id),
        )
        return ProposedAction(action_id, agent_name, action_type, "rejected", matter_id, None)

    handler = ACTION_HANDLERS.get(action_type)
    try:
        if handler is None:
            raise ValueError(f"no handler registered for action_type {action_type!r}")
        outcome = handler(conn, payload if isinstance(payload, dict) else json.loads(payload))
        conn.execute(
            "update app.proposed_actions set status='executed', decided_by=%s, decided_at=now(), "
            "outcome=%s where id=%s",
            (decided_by, json.dumps(outcome), action_id),
        )
        return ProposedAction(action_id, agent_name, action_type, "executed", matter_id, outcome)
    except Exception as exc:  # noqa: BLE001 — record the failure as the action outcome
        err = {"error": str(exc)}
        conn.execute(
            "update app.proposed_actions set status='failed', decided_by=%s, decided_at=now(), "
            "outcome=%s where id=%s",
            (decided_by, json.dumps(err), action_id),
        )
        return ProposedAction(action_id, agent_name, action_type, "failed", matter_id, err)
