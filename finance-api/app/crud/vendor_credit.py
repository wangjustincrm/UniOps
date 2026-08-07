"""Vendor Credit business rules.

This module is the ONLY place that normalises the sign of a credit amount.
Vendors print credit notes as negatives; the ledger stores positives. Doing it
here (rather than in the API layer, the frontend, or OCR) means every entry
path — manual upload today, QBO import in Phase C — gets the same treatment
and the CHECK constraint can be trusted.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.vendor_credit import (
    AVAILABLE, EXHAUSTED, PENDING_REVIEW, SOURCE_UPLOAD, VOID, VendorCredit,
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
    """The fast-path twin of uq_vendor_credits_vendor_docno.

    Its predicate must stay identical to the index's, or the two disagree and
    the IntegrityError fallback in create() cannot find the row that won: the
    index is NOT scoped by source (a manually uploaded credit and the same
    vendor document later pulled in by the Phase C QBO import are one document,
    not two), so neither is this.
    """
    return (await db.execute(
        select(VendorCredit).where(
            VendorCredit.vendor_id == vendor_id,
            VendorCredit.vendor_credit_number == doc_number,
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
    # The number's date component is the LOCAL date, matching every other
    # numbering helper in this service (app/crud/ap_invoice.py:23,
    # app/crud/ar.py:32). The company is in Toronto (UTC-4/-5), so a UTC date
    # here would stamp tomorrow's date on anything created after ~20:00 local.
    # uploaded_at stays a UTC timestamp.
    number = await next_number(
        db, VendorCredit.credit_number, f"VC-{date.today():%Y%m%d}-", 4)

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


async def get_for_update(db: AsyncSession,
                         credit_id: uuid.UUID) -> VendorCredit | None:
    """Row-locked load for the mutating review routes.

    void()'s "has this already been applied?" guard is check-then-act. Under a
    plain SELECT, a void running alongside a Phase B credit application reads
    applied_amount = 0, passes the guard, and then marks void a credit the
    payment has meanwhile consumed. No CHECK constraint catches it: void moves
    no monetary column, so ck_vendor_credits_balance and
    ck_vendor_credits_nonneg both still hold. FOR UPDATE serialises the two.
    Read-only routes deliberately do not take this lock.
    """
    return (await db.execute(
        select(VendorCredit).where(VendorCredit.id == credit_id).with_for_update()
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


class CreditUnavailable(ValueError):
    """An explicitly requested credit is not applicable to this payment.

    Inherits ValueError (not Exception) deliberately: `execute()`'s contract is
    "Raises LookupError (404), ValueError (409), PaymentPermissionError (403)",
    and a credit that has since been consumed or voided by the time the
    operator submits is exactly a 409 conflict — the transaction never
    commits, so no money moves, but the caller needs a message naming the
    credit rather than an unhandled 500. The same base class makes the batch
    runner's per-line `except (..., ValueError)` catch it too, isolating one
    bad line instead of aborting the whole run. Do not change this back to
    Exception.
    """


async def select_credits_for_payment(
    db: AsyncSession, *, vendor_id: uuid.UUID, currency: str, base: Decimal,
    credit_ids: list[uuid.UUID] | None = None, lock: bool = True,
) -> list[tuple[VendorCredit, Decimal]]:
    """Pick credits to net off a payment of `base`, oldest first.

    credit_ids is THREE-VALUED and must be compared with `is None`:
      None  -> automatic FIFO over every applicable credit
      []    -> apply nothing this run
      [ids] -> apply only these, and fail loudly if any is not applicable

    Locking differs by mode on purpose. The automatic path uses SKIP LOCKED so
    two concurrent batches paying the same vendor take disjoint credits instead
    of blocking each other. The explicit path blocks, because the operator was
    shown these exact credits and applying a subset silently would make the
    preview a lie. lock=False is for the read-only preview, which must not hold
    locks across a human's think time.
    """
    if credit_ids is not None and len(credit_ids) == 0:
        return []
    if base <= _ZERO:
        return []

    q = select(VendorCredit).where(
        VendorCredit.vendor_id == vendor_id,
        VendorCredit.currency == currency,
        VendorCredit.status == AVAILABLE,
        VendorCredit.remaining_amount > _ZERO,
    ).order_by(VendorCredit.credit_date, VendorCredit.created_at)

    if credit_ids is not None:
        q = q.where(VendorCredit.id.in_(credit_ids))
        if lock:
            q = q.with_for_update()
    elif lock:
        q = q.with_for_update(skip_locked=True)

    rows = list((await db.execute(q)).scalars().all())

    if credit_ids is not None:
        found = {r.id for r in rows}
        missing = [str(cid) for cid in credit_ids if cid not in found]
        if missing:
            raise CreditUnavailable(
                "These credits are no longer available for this payment: "
                + ", ".join(missing)
            )

    picks: list[tuple[VendorCredit, Decimal]] = []
    remaining_base = base
    for credit in rows:
        if remaining_base <= _ZERO:
            break
        take = min(credit.remaining_amount, remaining_base)
        if take <= _ZERO:
            continue
        picks.append((credit, take))
        remaining_base -= take
    return picks


async def apply_credits(
    db: AsyncSession, picks: list[tuple[VendorCredit, Decimal]], *,
    payment_record_id: uuid.UUID, batch_id: uuid.UUID | None,
    doc_kind: str, doc_id: uuid.UUID, doc_number: str | None,
    applied_by: uuid.UUID,
) -> Decimal:
    """Consume `picks` against one payment and return the total applied.

    Call this only AFTER the PaymentRecord has been flushed — its id is stored
    on every application row. Runs in the caller's transaction so the payment
    and the credit decrements commit or roll back together; a partial outcome
    here is money that exists in one place and not the other.
    """
    from app.models.vendor_credit import VendorCreditApplication

    total = _ZERO
    now = datetime.now(timezone.utc)
    for credit, take in picks:
        credit.applied_amount = credit.applied_amount + take
        credit.remaining_amount = credit.remaining_amount - take
        if credit.remaining_amount <= _ZERO:
            credit.status = EXHAUSTED
        db.add(VendorCreditApplication(
            credit_id=credit.id,
            payment_record_id=payment_record_id,
            batch_id=batch_id,
            doc_kind=doc_kind,
            doc_id=doc_id,
            doc_number=doc_number,
            applied_amount=take,
            applied_at=now,
            applied_by=applied_by,
        ))
        total += take
    return total
