"""Micro-request endpoints (WP 5.4) — @request, thread, resolve.

Everything is RLS-scoped: a request is visible to its two parties and the parent's team, and the
insert policy pins requester_id to the caller so nobody raises a request in someone else's name.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import micro_requests as mr
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/micro-requests", tags=["micro-requests"])


class CreateIn(BaseModel):
    assignee_id: UUID
    prompt: str
    parent_work_item_id: UUID | None = None
    parent_document_id: UUID | None = None
    sla_hours: float | None = mr.DEFAULT_SLA_HOURS


class MessageIn(BaseModel):
    body: str


class RequestOut(BaseModel):
    id: UUID
    status: str
    assignee_id: UUID
    requester_id: UUID


class MessageOut(BaseModel):
    id: UUID
    author_id: UUID
    body: str
    created_at: datetime


@router.post("", response_model=RequestOut, status_code=201)
def create(body: CreateIn, identity: Identity) -> RequestOut:
    with identity.connection() as conn:
        try:
            rid = mr.create_request(
                conn, UUID(identity.entra.os_user_id), body.assignee_id, body.prompt,
                parent_work_item_id=body.parent_work_item_id,
                parent_document_id=body.parent_document_id, sla_hours=body.sla_hours,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        row = conn.execute(
            "select id, status::text, assignee_id, requester_id from app.micro_requests "
            " where id = %s",
            (rid,),
        ).fetchone()
    assert row is not None
    return RequestOut(id=row[0], status=row[1], assignee_id=row[2], requester_id=row[3])


@router.get("/mine", response_model=list[RequestOut])
def mine(identity: Identity) -> list[RequestOut]:
    """The caller's outstanding requests as assignee — the intra-day queue."""
    with identity.connection() as conn:
        items = mr.open_requests_for(conn, UUID(identity.entra.os_user_id))
    return [
        RequestOut(id=i.id, status=i.status, assignee_id=i.assignee_id, requester_id=i.requester_id)
        for i in items
    ]


@router.get("/{request_id}/messages", response_model=list[MessageOut])
def messages(request_id: UUID, identity: Identity) -> list[MessageOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            "select id, author_id, body, created_at from app.micro_request_messages "
            " where request_id = %s order by created_at",
            (request_id,),
        ).fetchall()
    return [MessageOut(id=r[0], author_id=r[1], body=r[2], created_at=r[3]) for r in rows]


@router.post("/{request_id}/messages", response_model=RequestOut)
def post_message(request_id: UUID, body: MessageIn, identity: Identity) -> RequestOut:
    with identity.connection() as conn:
        try:
            mr.post_message(conn, request_id, UUID(identity.entra.os_user_id), body.body)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        row = conn.execute(
            "select id, status::text, assignee_id, requester_id from app.micro_requests "
            " where id = %s",
            (request_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="request not found")
    return RequestOut(id=row[0], status=row[1], assignee_id=row[2], requester_id=row[3])


@router.post("/{request_id}/resolve", status_code=204)
def resolve(request_id: UUID, identity: Identity) -> None:
    with identity.connection() as conn:
        try:
            mr.resolve_request(conn, request_id, UUID(identity.entra.os_user_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
