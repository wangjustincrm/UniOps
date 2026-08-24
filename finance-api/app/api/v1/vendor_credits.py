"""Vendor Credit API — record and review vendor credit notes.

Gating rationale (spec §8): creating and reading a CREDIT NOTE need only a
valid token, matching "if you can upload an invoice you can upload a credit
note". A created credit is `pending_review` and confers nothing until approved,
so the real gate sits on the review actions, which require
epms.vendor_credit.manage.

That openness covers credit-note data only. The two routes here that return
PAYMENT data — /suggest (a PA's gross and net) and /{id}/applications (which
payments a credit was consumed by, for how much, by whom) — are gated by
`authorize_finance_read`, exactly as app/api/v1/payments.py's own reads are.
Without it any authenticated employee, including OA-only users with no finance
role, could read payment amounts through this router.
"""
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.core.read_authz import authorize_finance_read
from app.crud import vendor_credit as crud
from app.db.base import get_db
from app.models.mirrors import User
from app.models.pa import PaymentApplication
from app.models.vendor_credit import VendorCreditApplication
from app.schemas.vendor_credit import (
    CreditSuggestion, CreditSuggestResponse, VendorCreditApplicationListResponse,
    VendorCreditApplicationRow, VendorCreditCreate, VendorCreditListResponse,
    VendorCreditReject, VendorCreditResponse, VendorCreditReview,
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


async def _load_for_update(db: AsyncSession, credit_id: uuid.UUID):
    """Used by approve/reject/void only. Their status and applied_amount guards
    are check-then-act and need the row locked for the length of the
    transaction — see crud.get_for_update. The GET routes stay unlocked."""
    credit = await crud.get_for_update(db, credit_id)
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


@router.get("/suggest", response_model=CreditSuggestResponse)
async def suggest_credits(user: CurrentUser,
                          doc_kind: str = Query(...),
                          doc_id: uuid.UUID = Query(...),
                          db: AsyncSession = Depends(get_db)):
    """Preview which credits would be netted off a payment, without locking.

    Declared before /{credit_id} so FastAPI does not try to parse the literal
    "suggest" as a UUID.

    Gated: the response carries the PA's gross and net, which is payment data
    (see the module docstring) — not the deliberately open credit-note data.
    """
    await authorize_finance_read(db, user)
    if doc_kind not in ("pa", "pa_dir"):
        raise HTTPException(
            status_code=422,
            detail="Vendor credits apply to vendor payments only (pa, pa_dir)")

    pa = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id == doc_id)
    )).scalar_one_or_none()
    if pa is None:
        raise HTTPException(status_code=404, detail="Payment application not found")

    picks = await crud.select_credits_for_payment(
        db, vendor_id=pa.vendor_id, currency=pa.currency,
        base=pa.payment_amount, credit_ids=None, lock=False,
    )
    # Decimal("0") has scale 0, so an empty `picks` would serialize
    # credit_applied as "0" instead of "0.00" — start the accumulator at
    # scale 2 to match pa.payment_amount's precision either way.
    applied = sum((take for _, take in picks), Decimal("0.00"))
    return CreditSuggestResponse(
        gross=pa.payment_amount,
        suggested=[
            CreditSuggestion(
                credit_id=c.id, credit_number=c.credit_number,
                vendor_credit_number=c.vendor_credit_number,
                credit_date=c.credit_date, remaining=c.remaining_amount, apply=take,
            )
            for c, take in picks
        ],
        credit_applied=applied,
        net=pa.payment_amount - applied,
    )


@router.get("/{credit_id}", response_model=VendorCreditResponse)
async def get_vendor_credit(credit_id: uuid.UUID, user: CurrentUser,
                            db: AsyncSession = Depends(get_db)):
    return await _load(db, credit_id)


@router.get("/{credit_id}/applications",
            response_model=VendorCreditApplicationListResponse)
async def list_vendor_credit_applications(credit_id: uuid.UUID, user: CurrentUser,
                                          db: AsyncSession = Depends(get_db)):
    """Where this credit went: one row per payment it was netted against.

    The only read path for `vendor_credit_applications`, and the only place
    inside the system that explains a short payment other than the remittance
    email sent to the vendor. Gated by `authorize_finance_read` — it names
    payment records, documents and amounts.
    """
    await authorize_finance_read(db, user)
    await _load(db, credit_id)          # 404 for an unknown credit, not an empty list

    rows = list((await db.execute(
        select(VendorCreditApplication)
        .where(VendorCreditApplication.credit_id == credit_id)
        .order_by(VendorCreditApplication.applied_at.desc())
    )).scalars().all())
    total_applied = (await db.execute(
        select(func.coalesce(func.sum(VendorCreditApplication.applied_amount),
                             Decimal("0.00")))
        .where(VendorCreditApplication.credit_id == credit_id)
    )).scalar_one()

    actor_ids = {r.applied_by for r in rows}
    names: dict[uuid.UUID, str] = {}
    if actor_ids:
        for u in (await db.execute(
            select(User).where(User.id.in_(actor_ids))
        )).scalars().all():
            names[u.id] = u.full_name or u.email

    items = []
    for r in rows:
        row = VendorCreditApplicationRow.model_validate(r)
        row.applied_by_name = names.get(r.applied_by)
        items.append(row)
    return VendorCreditApplicationListResponse(
        items=items, total=len(items), total_applied=total_applied)


@router.post("/{credit_id}/approve", response_model=VendorCreditResponse)
async def approve_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReview,
                                user: dict = Depends(require_permission(_MANAGE_KEY)),
                                db: AsyncSession = Depends(get_db)):
    credit = await _load_for_update(db, credit_id)
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
    credit = await _load_for_update(db, credit_id)
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
    credit = await _load_for_update(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.void(db, credit, reviewed_by=uid,
                                 reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit
