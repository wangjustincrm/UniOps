"""Corrective action persistence, assignment and closure."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action import Action, ActionUpdate, ActionVerification
from app.models.mirrors import Location, User
from app.schemas.action import ActionCreate, ActionUpdateIn, ActionVerifyIn
from app.services import tasks as task_service
from app.services.numbering import next_number

OPEN_STATUSES = ("open", "in_progress", "pending_verification")


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _location_path(db: AsyncSession, location_id: uuid.UUID | None) -> str | None:
    if location_id is None:
        return None
    return (await db.execute(select(Location.path).where(Location.id == location_id))).scalar()


async def create(
    db: AsyncSession,
    payload: ActionCreate,
    *,
    source_ref: str | None = None,
    now: datetime | None = None,
) -> Action:
    """Raise an action and put it in the owner's inbox.

    The inbox task is created in the same transaction as the action. An action
    that exists but that its owner never sees is the failure mode this module
    is meant to remove.
    """
    now = now or datetime.now(timezone.utc)
    action = Action(
        id=uuid.uuid4(),
        action_no=await next_number(db, "action", now=now),
        source_type=payload.source_type,
        source_id=payload.source_id,
        source_ref=source_ref,
        cause_id=payload.cause_id,
        action_type=payload.action_type,
        title=payload.title,
        description=payload.description,
        hierarchy_of_control=payload.hierarchy_of_control,
        priority=payload.priority,
        status="open",
        owner_id=payload.owner_id,
        owner_name=await _name(db, payload.owner_id),
        due_date=payload.due_date,
        location_id=payload.location_id,
        location_path=await _location_path(db, payload.location_id),
        department_id=payload.department_id,
        occurred_at=now,
    )
    db.add(action)
    await db.flush()

    await task_service.open_task(
        db,
        task_type=task_service.TASK_DO_ACTION,
        doc_type=task_service.DOC_ACTION,
        doc_id=action.id,
        doc_number=action.action_no,
        title=action.title,
        assigned_role="worker",
        assigned_user_id=action.owner_id,
        description=f"Due {action.due_date.isoformat()}"
                    + (f" — raised from {source_ref}" if source_ref else ""),
        priority=action.priority,
    )
    await db.flush()
    return action


async def get(db: AsyncSession, action_id: uuid.UUID) -> Action:
    action = (await db.execute(select(Action).where(Action.id == action_id))).scalar_one_or_none()
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    return action


async def list_(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID | None = None,
    statuses: list[str] | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    cause_id: uuid.UUID | None = None,
    overdue_only: bool = False,
    today: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Action]:
    stmt = select(Action)
    if owner_id:
        stmt = stmt.where(Action.owner_id == owner_id)
    if statuses:
        stmt = stmt.where(Action.status.in_(statuses))
    if source_type:
        stmt = stmt.where(Action.source_type == source_type)
    if source_id:
        stmt = stmt.where(Action.source_id == source_id)
    if cause_id:
        stmt = stmt.where(Action.cause_id == cause_id)
    if overdue_only:
        stamp = (today or datetime.now(timezone.utc)).date()
        stmt = stmt.where(Action.due_date < stamp, Action.status.in_(OPEN_STATUSES))
    stmt = stmt.order_by(Action.due_date).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def load_updates(db: AsyncSession, action_id: uuid.UUID) -> list[ActionUpdate]:
    return list((await db.execute(
        select(ActionUpdate).where(ActionUpdate.action_id == action_id)
        .order_by(ActionUpdate.created_at)
    )).scalars())


async def load_verifications(db: AsyncSession, action_id: uuid.UUID) -> list[ActionVerification]:
    return list((await db.execute(
        select(ActionVerification).where(ActionVerification.action_id == action_id)
        .order_by(ActionVerification.verified_at)
    )).scalars())


async def add_update(
    db: AsyncSession, action: Action, payload: ActionUpdateIn, *, user_id: uuid.UUID
) -> ActionUpdate:
    if action.status in ("closed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Action {action.action_no} is {action.status}")
    update = ActionUpdate(
        id=uuid.uuid4(),
        action_id=action.id,
        author_id=user_id,
        author_name=await _name(db, user_id),
        body=payload.body,
        file_ids=[str(f) for f in payload.file_ids],
        new_status=payload.new_status,
    )
    db.add(update)
    if payload.new_status:
        action.status = payload.new_status
        if payload.new_status == "pending_verification":
            # The doer is done; someone else has to confirm the control works.
            await task_service.close_tasks_for(
                db, doc_type=task_service.DOC_ACTION, doc_id=action.id,
                task_type=task_service.TASK_DO_ACTION, completed_by=user_id)
            await task_service.open_task(
                db,
                task_type=task_service.TASK_VERIFY_ACTION,
                doc_type=task_service.DOC_ACTION,
                doc_id=action.id,
                doc_number=action.action_no,
                title=f"Verify: {action.title}",
                assigned_role="ehs_coordinator",
                description="Confirm the control is effective, not just that the work was done.",
            )
    await db.flush()
    return update


async def verify(
    db: AsyncSession,
    action: Action,
    payload: ActionVerifyIn,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> ActionVerification:
    """Record a verification. Effective closes the action; ineffective reopens it.

    A verification that fails is the point of having verification at all — COR
    asks for evidence that a corrective action worked, not that somebody did
    something.
    """
    if action.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Action {action.action_no} is already closed")
    stamp = now or datetime.now(timezone.utc)
    verification = ActionVerification(
        id=uuid.uuid4(),
        action_id=action.id,
        verified_by=user_id,
        verified_by_name=await _name(db, user_id),
        verified_at=stamp,
        is_effective=payload.is_effective,
        evidence=payload.evidence,
        file_ids=[str(f) for f in payload.file_ids],
    )
    db.add(verification)

    if payload.is_effective:
        action.status = "closed"
        action.verified_at = stamp
        action.closed_at = stamp
        await task_service.close_tasks_for(
            db, doc_type=task_service.DOC_ACTION, doc_id=action.id, completed_by=user_id)
    else:
        action.status = "in_progress"
        await task_service.close_tasks_for(
            db, doc_type=task_service.DOC_ACTION, doc_id=action.id,
            task_type=task_service.TASK_VERIFY_ACTION, completed_by=user_id)
        await task_service.open_task(
            db,
            task_type=task_service.TASK_DO_ACTION,
            doc_type=task_service.DOC_ACTION,
            doc_id=action.id,
            doc_number=action.action_no,
            title=f"Reopened: {action.title}",
            assigned_role="worker",
            assigned_user_id=action.owner_id,
            description="Verification found the control was not effective.",
            priority="high",
        )
    await db.flush()
    return verification
