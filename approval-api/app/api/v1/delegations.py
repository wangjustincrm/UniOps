"""Admin REST surface for dated approval delegation (代班).

Owns `approval_delegations` (app/models/delegation.py). Mirrors
app/api/v1/routing.py's shape: same CurrentUser dependency, same
system_admin guard, all writes system_admin-only. Task 12's Portal admin
page consumes this surface.

The overlap-translation below is the one part that must not be skipped: the
table's ex_delegation_no_overlap GiST exclusion constraint is the real
authority on "no two live windows for one delegator may intersect", but a
raw asyncpg.ExclusionViolationError must never reach the caller as a 500
leaking the constraint name — it is translated into an actionable 409.
"""
import uuid
from datetime import datetime, timezone
from typing import NoReturn

from asyncpg.exceptions import ExclusionViolationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.delegation import ApprovalDelegation
from app.models.user import User
from app.schemas.delegation import DelegationCreate, DelegationOut, DelegationUpdate

router = APIRouter(prefix="/delegations", tags=["delegations"])

_Delegator = aliased(User)
_Delegate = aliased(User)


def _require_admin(user: dict) -> None:
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")


def _raise_for_integrity_error(exc: IntegrityError) -> NoReturn:
    """Translate the exclusion-constraint violation into a readable 409.

    Any other IntegrityError (self-delegation, date order) should already
    have been caught by the pre-flush validation below, so it is left to
    propagate rather than silently mis-labelled as an overlap.
    """
    orig = getattr(exc, "orig", None)
    if isinstance(getattr(orig, "__cause__", None), ExclusionViolationError):
        raise HTTPException(
            status_code=409,
            detail="This person already has a delegation covering part of "
                   "that date range. Revoke or shorten it first.",
        ) from exc
    raise exc


def _select_with_names():
    return (
        select(
            ApprovalDelegation,
            _Delegator.full_name.label("delegator_name"),
            _Delegate.full_name.label("delegate_name"),
        )
        .outerjoin(_Delegator, _Delegator.id == ApprovalDelegation.delegator_user_id)
        .outerjoin(_Delegate, _Delegate.id == ApprovalDelegation.delegate_user_id)
    )


def _to_out(row: ApprovalDelegation, delegator_name: str | None, delegate_name: str | None) -> DelegationOut:
    return DelegationOut(
        id=row.id,
        delegator_user_id=row.delegator_user_id,
        delegate_user_id=row.delegate_user_id,
        delegator_name=delegator_name,
        delegate_name=delegate_name,
        start_date=row.start_date,
        end_date=row.end_date,
        note=row.note,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _serialize_one(db: AsyncSession, delegation_id: uuid.UUID) -> DelegationOut:
    row, delegator_name, delegate_name = (await db.execute(
        _select_with_names().where(ApprovalDelegation.id == delegation_id)
    )).one()
    return _to_out(row, delegator_name, delegate_name)


async def _get_or_404(db: AsyncSession, delegation_id: uuid.UUID) -> ApprovalDelegation:
    row = (await db.execute(
        select(ApprovalDelegation).where(ApprovalDelegation.id == delegation_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Delegation not found")
    return row


@router.get("", response_model=list[DelegationOut])
async def list_delegations(db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    """All delegations (active and revoked), newest window first. Includes
    revoked rows so the admin page can show history, not just what's live."""
    _require_admin(user)
    rows = (await db.execute(
        _select_with_names().order_by(ApprovalDelegation.start_date.desc())
    )).all()
    return [_to_out(row, delegator_name, delegate_name) for row, delegator_name, delegate_name in rows]


@router.post("", response_model=DelegationOut, status_code=201)
async def create_delegation(
    body: DelegationCreate, db: AsyncSession = Depends(get_db), user: CurrentUser = ...,
):
    _require_admin(user)
    if body.delegator_user_id == body.delegate_user_id:
        raise HTTPException(status_code=422, detail="A delegator cannot delegate to themself.")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date.")

    row = ApprovalDelegation(
        id=uuid.uuid4(),
        delegator_user_id=body.delegator_user_id,
        delegate_user_id=body.delegate_user_id,
        start_date=body.start_date,
        end_date=body.end_date,
        note=body.note,
        created_by=uuid.UUID(user["sub"]),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_for_integrity_error(exc)
    await db.commit()
    return await _serialize_one(db, row.id)


@router.patch("/{delegation_id}", response_model=DelegationOut)
async def update_delegation(
    delegation_id: uuid.UUID, body: DelegationUpdate,
    db: AsyncSession = Depends(get_db), user: CurrentUser = ...,
):
    """Adjust the dates/note of a still-live delegation. Revoked rows are the
    audit trail, not editable — revoke and create a new one instead."""
    _require_admin(user)
    row = await _get_or_404(db, delegation_id)
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This delegation is already revoked and cannot be edited. "
                   "Create a new one instead.",
        )

    new_start = body.start_date if body.start_date is not None else row.start_date
    new_end = body.end_date if body.end_date is not None else row.end_date
    if new_end < new_start:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date.")

    if body.start_date is not None:
        row.start_date = body.start_date
    if body.end_date is not None:
        row.end_date = body.end_date
    if body.note is not None:
        row.note = body.note

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        _raise_for_integrity_error(exc)
    await db.commit()
    return await _serialize_one(db, row.id)


@router.post("/{delegation_id}/revoke", response_model=DelegationOut)
async def revoke_delegation(
    delegation_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: CurrentUser = ...,
):
    """Set revoked_at/revoked_by rather than deleting — the audit trail is
    the point. Idempotent: revoking an already-revoked row is a no-op 200
    that returns the original revoke's actor/timestamp unchanged."""
    _require_admin(user)
    row = await _get_or_404(db, delegation_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_by = uuid.UUID(user["sub"])
        await db.flush()
    await db.commit()
    return await _serialize_one(db, row.id)
