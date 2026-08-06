"""Vendor Credit business rules.

This module is the ONLY place that normalises the sign of a credit amount.
Vendors print credit notes as negatives; the ledger stores positives. Doing it
here (rather than in the API layer, the frontend, or OCR) means every entry
path — manual upload today, QBO import in Phase C — gets the same treatment
and the CHECK constraint can be trusted.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.vendor_credit import (
    AVAILABLE, PENDING_REVIEW, SOURCE_UPLOAD, VOID, VendorCredit,
)
from app.schemas.vendor_credit import VendorCreditCreate

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


class DuplicateCredit(Exception):
    """Same vendor already has a live credit with this document number."""

    def __init__(self, existing: VendorCredit):
        self.existing = existing
        super().__init__(
            f"Credit note {existing.vendor_credit_number} already recorded "
            f"as {existing.credit_number}"
        )


def _positive(v: Decimal) -> Decimal:
    return abs(Decimal(v)).quantize(_CENT)


async def _find_duplicate(db: AsyncSession, vendor_id: uuid.UUID,
                          doc_number: str) -> VendorCredit | None:
    return (await db.execute(
        select(VendorCredit).where(
            VendorCredit.vendor_id == vendor_id,
            VendorCredit.vendor_credit_number == doc_number,
            VendorCredit.source == SOURCE_UPLOAD,
            VendorCredit.status != VOID,
        )
    )).scalars().first()


async def create(db: AsyncSession, *, payload: VendorCreditCreate,
                 uploaded_by: uuid.UUID,
                 uploaded_by_name: str | None) -> VendorCredit:
    amount = _positive(payload.amount)
    tax = _positive(payload.tax_amount)
    total = amount + tax
    if total == _ZERO:
        raise ValueError("Credit total must not be zero")

    dup = await _find_duplicate(db, payload.vendor_id, payload.vendor_credit_number)
    if dup is not None:
        raise DuplicateCredit(dup)

    now = datetime.now(timezone.utc)
    number = await next_number(
        db, VendorCredit.credit_number, f"VC-{now.date():%Y%m%d}-", 4)

    vc = VendorCredit(
        credit_number=number,
        vendor_id=payload.vendor_id,
        vendor_name=payload.vendor_name,
        vendor_credit_number=payload.vendor_credit_number,
        credit_date=payload.credit_date,
        currency=payload.currency,
        amount=amount,
        tax_amount=tax,
        total_amount=total,
        applied_amount=_ZERO,
        remaining_amount=total,
        status=PENDING_REVIEW,
        po_id=payload.po_id,
        po_number=payload.po_number,
        line_items=payload.line_items,
        file_name=payload.file_name,
        notes=payload.notes,
        source=SOURCE_UPLOAD,
        opening_balance=False,
        uploaded_by=uploaded_by,
        uploaded_by_name=uploaded_by_name,
        uploaded_at=now,
    )
    # The _find_duplicate check above is check-then-act, not atomic: two
    # concurrent create() calls for the same (vendor_id, vendor_credit_number)
    # can both pass that SELECT before either commits. The loser would then
    # violate uq_vendor_credits_vendor_docno at flush and raise a raw
    # IntegrityError — but callers (the API route) only handle
    # DuplicateCredit and would surface that as a 500 instead of a 409. A
    # SAVEPOINT (begin_nested) scopes the failure to just this insert, so
    # catching it here still leaves the outer session/transaction usable:
    # we re-query for the row that won the race and raise DuplicateCredit
    # from it, same as the non-raced path above.
    try:
        async with db.begin_nested():
            db.add(vc)
            await db.flush()
    except IntegrityError:
        dup = await _find_duplicate(db, payload.vendor_id, payload.vendor_credit_number)
        if dup is not None:
            raise DuplicateCredit(dup) from None
        raise
    return vc


class InvalidTransition(Exception):
    """The requested review action is not legal from the current status."""


def _stamp_review(credit: VendorCredit, reviewed_by: uuid.UUID,
                  reviewed_by_name: str | None, note: str | None) -> None:
    credit.reviewed_by = reviewed_by
    credit.reviewed_by_name = reviewed_by_name
    credit.reviewed_at = datetime.now(timezone.utc)
    if note is not None:
        credit.review_note = note


async def approve(db: AsyncSession, credit: VendorCredit, *,
                  reviewed_by: uuid.UUID, reviewed_by_name: str | None,
                  note: str | None) -> VendorCredit:
    # Self-review is deliberately permitted: the AP team is small enough that a
    # segregation-of-duties gate would deadlock the queue. See spec §8.
    if credit.status != PENDING_REVIEW:
        raise InvalidTransition(
            f"Only a pending_review credit can be approved (is {credit.status})")
    credit.status = AVAILABLE
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit


async def reject(db: AsyncSession, credit: VendorCredit, *,
                 reviewed_by: uuid.UUID, reviewed_by_name: str | None,
                 note: str) -> VendorCredit:
    if credit.status != PENDING_REVIEW:
        raise InvalidTransition(
            f"Only a pending_review credit can be rejected (is {credit.status})")
    credit.status = VOID
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit


async def void(db: AsyncSession, credit: VendorCredit, *,
               reviewed_by: uuid.UUID, reviewed_by_name: str | None,
               note: str) -> VendorCredit:
    if credit.status != AVAILABLE:
        raise InvalidTransition(
            f"Only an available credit can be voided (is {credit.status})")
    if credit.applied_amount > _ZERO:
        raise InvalidTransition(
            "Credit has already been applied to a payment and cannot be voided")
    credit.status = VOID
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit


async def get_by_id(db: AsyncSession, credit_id: uuid.UUID) -> VendorCredit | None:
    return (await db.execute(
        select(VendorCredit).where(VendorCredit.id == credit_id)
    )).scalars().first()


async def get_all(db: AsyncSession, *, status: str | None = None,
                  vendor_id: uuid.UUID | None = None,
                  limit: int = 100, offset: int = 0) -> tuple[list[VendorCredit], int]:
    q = select(VendorCredit)
    c = select(func.count()).select_from(VendorCredit)
    if status:
        q = q.where(VendorCredit.status == status)
        c = c.where(VendorCredit.status == status)
    if vendor_id:
        q = q.where(VendorCredit.vendor_id == vendor_id)
        c = c.where(VendorCredit.vendor_id == vendor_id)
    total = (await db.execute(c)).scalar_one()
    rows = (await db.execute(
        q.order_by(VendorCredit.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return list(rows), total
