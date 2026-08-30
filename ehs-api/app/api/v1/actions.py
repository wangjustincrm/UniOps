"""Corrective and preventive action endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.permissions import CanReadAction, CanVerifyAction, CanWriteAction
from app.crud import action as crud
from app.schemas.action import (
    ActionCreate,
    ActionDetail,
    ActionOut,
    ActionUpdateIn,
    ActionUpdateOut,
    ActionVerificationOut,
    ActionVerifyIn,
)

router = APIRouter()


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


async def _detail(db, action) -> ActionDetail:
    detail = ActionDetail.model_validate(action)
    detail.updates = [ActionUpdateOut.model_validate(u) for u in await crud.load_updates(db, action.id)]
    detail.verifications = [
        ActionVerificationOut.model_validate(v) for v in await crud.load_verifications(db, action.id)
    ]
    return detail


@router.post("", response_model=ActionDetail, status_code=status.HTTP_201_CREATED)
async def create_action(payload: ActionCreate, db: SessionDep, user: CanWriteAction):  # noqa: ARG001
    action = await crud.create(db, payload)
    return await _detail(db, action)


@router.get("", response_model=list[ActionOut])
async def list_actions(
    db: SessionDep,
    user: CanReadAction,  # noqa: ARG001
    owner_id: uuid.UUID | None = None,
    status_in: list[str] | None = Query(default=None, alias="status"),
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    cause_id: uuid.UUID | None = None,
    overdue: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    rows = await crud.list_(
        db, owner_id=owner_id, statuses=status_in, source_type=source_type,
        source_id=source_id, cause_id=cause_id, overdue_only=overdue,
        limit=limit, offset=offset,
    )
    return [ActionOut.model_validate(r) for r in rows]


@router.get("/mine", response_model=list[ActionOut])
async def my_actions(
    db: SessionDep,
    user: CanReadAction,
    include_closed: bool = False,
):
    """What is assigned to the caller — the landing page for most employees."""
    statuses = None if include_closed else list(crud.OPEN_STATUSES)
    rows = await crud.list_(db, owner_id=_user_id(user), statuses=statuses, limit=200)
    return [ActionOut.model_validate(r) for r in rows]


@router.get("/{action_id}", response_model=ActionDetail)
async def get_action(action_id: uuid.UUID, db: SessionDep, user: CanReadAction):  # noqa: ARG001
    return await _detail(db, await crud.get(db, action_id))


@router.post("/{action_id}/updates", response_model=ActionDetail)
async def add_update(
    action_id: uuid.UUID, payload: ActionUpdateIn, db: SessionDep, user: CanReadAction,
):
    """Progress notes and evidence.

    Gated on read rather than write: the person doing the work has to be able
    to report on it, and they are frequently not the person who may raise or
    reassign actions.
    """
    action = await crud.get(db, action_id)
    await crud.add_update(db, action, payload, user_id=_user_id(user))
    return await _detail(db, action)


@router.post("/{action_id}/verify", response_model=ActionDetail)
async def verify_action(
    action_id: uuid.UUID, payload: ActionVerifyIn, db: SessionDep, user: CanVerifyAction,
):
    """Confirm the control is effective — or that it is not, which reopens it."""
    action = await crud.get(db, action_id)
    await crud.verify(db, action, payload, user_id=_user_id(user),
                      now=datetime.now(timezone.utc))
    return await _detail(db, action)
