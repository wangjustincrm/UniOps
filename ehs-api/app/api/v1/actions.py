"""Corrective and preventive action endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import SessionDep
from uniops_authz import has_permission

from app.core.permissions import (
    CanReadAction, CanReportIncident, CanVerifyAction, CanWriteAction,
)
from app.crud import action as crud
from app.models.mirrors import Task
from app.services import tasks as task_service
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


async def _may_browse(db, payload: dict) -> bool:
    """Whether this caller sees the whole action register or only their own.

    ehs.incident.read is the marker for "can browse the register" — HSE, area
    supervisors, JHSC members and auditors hold it; production workers do not.
    A worker holds ehs.action.read so they can open and update the actions
    assigned to them, which is not the same as reading everybody's.
    """
    return await has_permission(
        db, _user_id(payload), payload.get("role", ""), "ehs.incident.read")


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
    user: CanReadAction,
    owner_id: uuid.UUID | None = None,
    status_in: list[str] | None = Query(default=None, alias="status"),
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    cause_id: uuid.UUID | None = None,
    overdue: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    # Without the browse marker the list narrows to your own rather than
    # refusing — the same shape as the incident register.
    if not await _may_browse(db, user):
        owner_id = _user_id(user)
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


class AssignedTaskOut(BaseModel):
    """One thing assigned to this person that is not a corrective action."""

    id: uuid.UUID
    type: str
    title: str
    description: str | None
    priority: str
    document_type: str
    document_id: uuid.UUID
    document_number: str
    created_at: datetime
    # Where to go to deal with it; empty when this module has no screen for it.
    path: str


# Where each kind of work is actually done. A task whose type is not here is
# still listed — it says what it is and who it is for — but without a link,
# which is better than a link that goes nowhere.
TASK_PATHS: dict[str, str] = {
    "ehs_tbt": "/toolbox-talks",
    "ehs_run": "/forms",
    "ehs_doc": "/documents/{id}",
    "ehs_crs": "/training",
    "ehs_cert": "/training",
    "ehs_asset": "/equipment",
    "ehs_inc": "/incidents/{id}",
    "ehs_jhsc": "/jhsc",
    "ehs_ptw": "/permits/{id}",
}


@router.get("/mine/tasks", response_model=list[AssignedTaskOut])
async def my_tasks(db: SessionDep, user: CanReportIncident):
    """Everything else assigned to me by this module.

    "My Actions" listed corrective actions only, and every other thing the
    module hands somebody — a toolbox talk to deliver, an inspection due, a
    policy to sign — went to the `tasks` table and appeared nowhere on the page
    named after being assigned work. The first acceptance round found it on the
    toolbox talk: the tasks were created correctly, eleven of them, and the
    supervisor had no screen that showed them.
    """
    rows = (await db.execute(
        select(Task).where(
            Task.assigned_user_id == _user_id(user),
            Task.is_completed.is_(False),
            Task.document_type.like("ehs%"),
            # Not corrective actions: those are the list this sits under, and
            # showing the same work twice is its own kind of wrong.
            Task.document_type != task_service.DOC_ACTION,
        ).order_by(Task.created_at.desc())
    )).scalars().all()

    out = []
    for t in rows:
        template = TASK_PATHS.get(t.document_type)
        out.append(AssignedTaskOut(
            id=t.id, type=t.type, title=t.title, description=t.description,
            priority=t.priority, document_type=t.document_type,
            document_id=t.document_id, document_number=t.document_number,
            created_at=t.created_at,
            path=template.format(id=t.document_id) if template else "",
        ))
    return out


@router.get("/{action_id}", response_model=ActionDetail)
async def get_action(action_id: uuid.UUID, db: SessionDep, user: CanReadAction):
    action = await crud.get(db, action_id)
    if action.owner_id != _user_id(user) and not await _may_browse(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open corrective actions assigned to you",
        )
    return await _detail(db, action)


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
    if action.owner_id != _user_id(user) and not await _may_browse(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only report progress on actions assigned to you",
        )
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
