"""Orchestrator (A0) endpoints — agent registry + approval queue (WP 6.1).

The human approval gate on anything an agent sends/files/invoices/instructs (spec principle).
RLS-scoped throughout (D44): the queue runs on the caller's connection, so an approver only sees
and acts on proposals for matters they can see. Approve = execute the action.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from py_shared.orchestrator import (
    AgentDisabled,
    CredentialDenied,
    decide_action,
    get_agent,
    propose_action,
)
from pydantic import BaseModel

from app.deps import Identity
from app.errors import map_db_error

router = APIRouter(prefix="/api/v1", tags=["orchestrator"])


class AgentOut(BaseModel):
    name: str
    purpose: str
    enabled: bool
    allowed_actions: list[str]
    allowed_secret_slots: list[str]


class ProposeRequest(BaseModel):
    agent_name: str
    action_type: str
    payload: dict[str, Any] = {}
    matter_id: UUID | None = None
    family_id: UUID | None = None
    confidence: float | None = None


class ProposalOut(BaseModel):
    id: UUID
    agent_name: str
    action_type: str
    matter_id: UUID | None
    status: str
    confidence: float | None
    payload: dict[str, Any]


class DecisionOut(BaseModel):
    id: UUID
    status: str
    outcome: dict[str, Any] | None


@router.get("/agents", response_model=list[AgentOut])
def list_agents(identity: Identity) -> list[AgentOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            "select name, purpose, enabled, allowed_actions, allowed_secret_slots "
            "from ops.agents order by name"
        ).fetchall()
    return [
        AgentOut(name=r[0], purpose=r[1], enabled=r[2], allowed_actions=r[3],
                 allowed_secret_slots=r[4])
        for r in rows
    ]


@router.get("/approvals", response_model=list[ProposalOut])
def list_approvals(
    identity: Identity, status: str = "proposed", matter_id: UUID | None = None,
) -> list[ProposalOut]:
    """The approval queue, RLS-scoped to matters the caller can see."""
    with identity.connection() as conn:
        rows = conn.execute(
            """
            select id, agent_name, action_type, matter_id, status::text, confidence, payload
              from app.proposed_actions
             where status = %(status)s
               and (%(matter_id)s::uuid is null or matter_id = %(matter_id)s)
             order by proposed_at
             limit 500
            """,
            {"status": status, "matter_id": matter_id},
        ).fetchall()
    return [
        ProposalOut(id=r[0], agent_name=r[1], action_type=r[2], matter_id=r[3], status=r[4],
                    confidence=r[5], payload=r[6])
        for r in rows
    ]


@router.post("/approvals", response_model=ProposalOut, status_code=201)
def propose(body: ProposeRequest, identity: Identity) -> ProposalOut:
    """Enqueue a proposed action (agents normally do this via a system worker; exposed here for the
    user path + tests). Enforces the agent kill switch + allowed-actions allow-list."""
    try:
        with identity.connection() as conn:
            action_id = propose_action(
                conn, body.agent_name, body.action_type, body.payload,
                matter_id=body.matter_id, family_id=body.family_id, confidence=body.confidence,
            )
            row = conn.execute(
                "select id, agent_name, action_type, matter_id, status::text, confidence, payload "
                "from app.proposed_actions where id = %s",
                (action_id,),
            ).fetchone()
    except (CredentialDenied, AgentDisabled) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    assert row is not None
    return ProposalOut(id=row[0], agent_name=row[1], action_type=row[2], matter_id=row[3],
                       status=row[4], confidence=row[5], payload=row[6])


def _decide(identity: Identity, action_id: UUID, approve: bool) -> DecisionOut:
    try:
        with identity.connection() as conn:
            result = decide_action(conn, action_id, approve, identity.entra.os_user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Proposed action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise map_db_error(exc) from exc
    return DecisionOut(id=result.id, status=result.status, outcome=result.outcome)


@router.post("/approvals/{action_id}/approve", response_model=DecisionOut)
def approve(action_id: UUID, identity: Identity) -> DecisionOut:
    """Approve = execute the action via its registered handler, recording the outcome."""
    return _decide(identity, action_id, approve=True)


@router.post("/approvals/{action_id}/reject", response_model=DecisionOut)
def reject(action_id: UUID, identity: Identity) -> DecisionOut:
    return _decide(identity, action_id, approve=False)


# get_agent re-exported for other routers that need registry lookups.
__all__ = ["router", "get_agent"]
