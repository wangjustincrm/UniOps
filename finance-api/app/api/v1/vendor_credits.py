"""Vendor Credit API — record and review vendor credit notes.

Gating rationale (spec §8): creating and reading need only a valid token,
matching "if you can upload an invoice you can upload a credit note". A created
credit is `pending_review` and confers nothing until approved, so the real gate
sits on the review actions, which require epms.vendor_credit.manage.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.crud import vendor_credit as crud
from app.db.base import get_db
from app.schemas.vendor_credit import (
    VendorCreditCreate, VendorCreditListResponse, VendorCreditReject,
    VendorCreditResponse, VendorCreditReview,
)

router = APIRouter(prefix="/vendor-credits", tags=["vendor-credits"])

_MANAGE_KEY = "epms.vendor_credit.manage"


def _actor(user: dict) -> tuple[uuid.UUID, str | None]:
    try:
        uid = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token has no usable subject")
    return uid, user.get("full_name") or user.get("email")


async def _load(db: AsyncSession, credit_id: uuid.UUID):
    credit = await crud.get_by_id(db, credit_id)
    if credit is None:
        raise HTTPException(status_code=404, detail="Vendor credit not found")
    return credit


@router.post("", response_model=VendorCreditResponse, status_code=201)
async def create_vendor_credit(payload: VendorCreditCreate, user: CurrentUser,
                               db: AsyncSession = Depends(get_db)):
    uid, name = _actor(user)
    try:
        credit = await crud.create(db, payload=payload, uploaded_by=uid,
                                   uploaded_by_name=name)
    except crud.DuplicateCredit as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.get("", response_model=VendorCreditListResponse)
async def list_vendor_credits(user: CurrentUser,
                              status: str | None = Query(default=None),
                              vendor_id: uuid.UUID | None = Query(default=None),
                              limit: int = Query(default=100, ge=1, le=500),
                              offset: int = Query(default=0, ge=0),
                              db: AsyncSession = Depends(get_db)):
    items, total = await crud.get_all(db, status=status, vendor_id=vendor_id,
                                      limit=limit, offset=offset)
    return VendorCreditListResponse(items=items, total=total)


@router.get("/{credit_id}", response_model=VendorCreditResponse)
async def get_vendor_credit(credit_id: uuid.UUID, user: CurrentUser,
                            db: AsyncSession = Depends(get_db)):
    return await _load(db, credit_id)


@router.post("/{credit_id}/approve", response_model=VendorCreditResponse)
async def approve_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReview,
                                user: dict = Depends(require_permission(_MANAGE_KEY)),
                                db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.approve(db, credit, reviewed_by=uid,
                                    reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.post("/{credit_id}/reject", response_model=VendorCreditResponse)
async def reject_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReject,
                               user: dict = Depends(require_permission(_MANAGE_KEY)),
                               db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.reject(db, credit, reviewed_by=uid,
                                   reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.post("/{credit_id}/void", response_model=VendorCreditResponse)
async def void_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReject,
                             user: dict = Depends(require_permission(_MANAGE_KEY)),
                             db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.void(db, credit, reviewed_by=uid,
                                 reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit
