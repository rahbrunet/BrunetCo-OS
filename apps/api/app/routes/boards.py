"""Board framework endpoints (WP 5.3, spec §M9).

Boards, columns, views and automations are configuration; the interesting endpoints are creating
an automation (validated before it can ever fire) and setting a typed field value (validated
before storage). Everything runs on the caller's RLS-scoped connection — board visibility follows
scope, field values follow the annotated item.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from py_shared.domain import boards
from pydantic import BaseModel

from app.deps import Identity

router = APIRouter(prefix="/api/v1/boards", tags=["boards"])


class BoardIn(BaseModel):
    name: str
    scope_type: str
    scope_id: UUID | None = None


class BoardOut(BaseModel):
    id: UUID
    name: str
    scope_type: str
    scope_id: UUID | None


class FieldValueIn(BaseModel):
    value: Any


class AutomationIn(BaseModel):
    name: str
    trigger: dict[str, Any]
    actions: list[dict[str, Any]]


class AutomationOut(BaseModel):
    id: UUID
    name: str
    enabled: bool


@router.get("", response_model=list[BoardOut])
def list_boards(identity: Identity) -> list[BoardOut]:
    with identity.connection() as conn:
        rows = conn.execute(
            "select id, name, scope_type::text, scope_id from app.boards order by created_at"
        ).fetchall()
    return [BoardOut(id=r[0], name=r[1], scope_type=r[2], scope_id=r[3]) for r in rows]


@router.post("", response_model=BoardOut, status_code=201)
def create_board(body: BoardIn, identity: Identity) -> BoardOut:
    with identity.connection() as conn:
        try:
            row = conn.execute(
                "insert into app.boards (name, scope_type, scope_id, created_by) "
                "values (%s, %s::app.board_scope, %s, %s) returning id, scope_type::text, scope_id",
                (body.name, body.scope_type, body.scope_id, identity.entra.os_user_id),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 — bad scope/enum is a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        assert row is not None
    return BoardOut(id=row[0], name=body.name, scope_type=row[1], scope_id=row[2])


@router.get("/{board_id}/items", response_model=list[UUID])
def board_items(board_id: UUID, identity: Identity) -> list[UUID]:
    """The work items this board lenses, derived from its scope (RLS still filters)."""
    with identity.connection() as conn:
        try:
            return boards.board_work_item_ids(conn, board_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/columns/{column_id}/items/{item_id}", status_code=204)
def set_field(column_id: UUID, item_id: UUID, body: FieldValueIn, identity: Identity) -> None:
    """Set a custom column value on a work item — validated against the column's type."""
    with identity.connection() as conn:
        try:
            boards.set_field_value(conn, item_id, column_id, body.value)
        except boards.FieldValueInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{board_id}/automations", response_model=AutomationOut, status_code=201)
def create_automation(board_id: UUID, body: AutomationIn, identity: Identity) -> AutomationOut:
    """Create a no-code rule. Validated before storage, so a board never carries a rule that will
    throw when it fires."""
    try:
        boards.validate_automation(body.trigger, body.actions)
    except boards.AutomationInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import json

    with identity.connection() as conn:
        row = conn.execute(
            "insert into app.board_automations (board_id, name, trigger, actions, created_by) "
            "values (%s, %s, %s, %s, %s) returning id, enabled",
            (board_id, body.name, json.dumps(body.trigger), json.dumps(body.actions),
             identity.entra.os_user_id),
        ).fetchone()
        assert row is not None
    return AutomationOut(id=row[0], name=body.name, enabled=row[1])
