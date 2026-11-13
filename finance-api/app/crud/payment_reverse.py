"""Reverse a payment that was recorded but never sent.

The case it exists for (2026-11-10, BP-20261110-0003 / PA-20261019-0006): a
batch was executed in UniOps, the money never left the bank, and Finance wanted
to pay the PA again. Deleting the batch in Data Maintenance removed only the
batch and its lines; everything execute() wrote stayed behind — a `completed`
payment_record (which _refuse_second_payment then uses to refuse the re-pay),
paid invoices / AP rows, the posting event and its draft JV, the closed
process_pa task. It took hand-written SQL on production to undo.

This module is the inverse of payment_execute.execute() for PA payments, and
undoes exactly what that function wrote:

  payment_record      → status 'cancelled' (kept, with the reason, as history)
  PA                  → 'processed' back to 'approved', paid_at cleared
  invoices / AP rows  → back to matched / posted, unless another completed
                        payment still covers them
  vendor credits      → applications removed, amounts given back
  posting event + JV  → deleted

The posting event is DELETED, not mirrored with a reversal event: emit_event()
is idempotent on (source_doc_type, source_doc_id, event_type), so with the old
`payment` event still there the re-payment's event would be silently skipped
and the GL would keep the old date and bank. Deleting is only correct while
its JV is still a draft — nothing has been booked. A JV that has been reviewed
or posted is a ledger entry an accountant owns; that refuses, and so does a
payment already matched in bank reconciliation (the money evidently moved).

Expense-claim payments are refused for now: their payment also booked actual
spend in budget-api over HTTP, and budget-api has no way to take it back.
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plant_time import fmt_datetime
from app.crud import payment_execute
from app.models.ap_invoice import ApInvoice
from app.models.bank import BankTransaction
from app.models.journal_voucher import DRAFT as JV_DRAFT
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine
from app.models.mirrors import Invoice, Task
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.payment_batch import EXECUTED, REVERSED, PaymentBatch, PaymentBatchLine
from app.models.posting import PostingEvent
from app.models.vendor_credit import (
    AVAILABLE,
    EXHAUSTED,
    VendorCredit,
    VendorCreditApplication,
)

CANCELLED = "cancelled"
_PA_KINDS = ("pa", "pa_dir")


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def _payment_event(db: AsyncSession, record: PaymentRecord) -> PostingEvent | None:
    # Same key execute() emitted with: the PA for a whole payment, the record
    # for an invoice-by-invoice one.
    source_id = record.pa_id if record.invoice_ids is None else record.id
    return (await db.execute(
        select(PostingEvent).where(
            PostingEvent.source_doc_type == record.doc_kind,
            PostingEvent.source_doc_id == source_id,
            PostingEvent.event_type == "payment",
        )
    )).scalar_one_or_none()


async def _check_reversible(db: AsyncSession, record: PaymentRecord,
                            ev: PostingEvent | None, jv: JournalVoucher | None) -> None:
    label = record.doc_number or str(record.id)
    if record.status != "completed":
        raise ValueError(f"{label}: payment is '{record.status}', not completed — nothing to reverse")
    if record.doc_kind not in _PA_KINDS:
        raise ValueError(
            f"{label}: expense-claim payments cannot be reversed here yet — the payment "
            "also booked actual spend in Budget, which cannot be taken back automatically.")
    if (await db.execute(select(sa.func.count()).select_from(BankTransaction).where(
            BankTransaction.matched_payment_id == record.id))).scalar_one():
        raise ValueError(f"{label}: this payment is matched to a bank statement line, so the money moved")
    if jv is not None:
        if jv.status != JV_DRAFT:
            raise ValueError(
                f"{label}: its journal voucher is already {jv.status} — reverse it in the "
                "ledger with an accountant, not here.")
        matched = (await db.execute(sa.text(
            "SELECT count(*) FROM bank_recon_match_books b JOIN journal_voucher_lines l"
            " ON l.id = b.jv_line_id WHERE l.jv_id = :j"), {"j": jv.id})).scalar_one()
        if matched:
            raise ValueError(f"{label}: its journal voucher is matched in bank reconciliation")
    await payment_execute._check_period_open(db, record.payment_date)


async def _still_covered(db: AsyncSession, inv_id: str, exclude: uuid.UUID) -> str | None:
    """What other completed payments still say about this invoice: 'paid',
    'partially_paid' (only prepayments cover it), or None (nothing does)."""
    rows = (await db.execute(
        select(PaymentApplication.pa_type, PaymentRecord.invoice_ids)
        .join(PaymentApplication, PaymentApplication.id == PaymentRecord.pa_id)
        .where(PaymentRecord.status == "completed", PaymentRecord.id != exclude,
               sa.or_(PaymentRecord.invoice_ids.contains([inv_id]),
                      sa.and_(PaymentRecord.invoice_ids.is_(None),
                              PaymentApplication.invoice_ids.contains([inv_id]))))
    )).all()
    if not rows:
        return None
    return "paid" if any(t != "prepayment" for t, _ in rows) else "partially_paid"


async def _undo_invoice_writeback(db: AsyncSession, pa: PaymentApplication,
                                  record: PaymentRecord) -> None:
    ids = record.invoice_ids if record.invoice_ids is not None else (pa.invoice_ids or [])
    for raw in ids:
        inv_id = _uuid(raw)
        if inv_id is None:
            continue
        remaining = await _still_covered(db, str(inv_id), record.id)
        if not pa.is_direct:
            inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one_or_none()
            if inv is not None and inv.status in ("paid", "partially_paid"):
                inv.status = remaining or "matched"
        ap = (await db.execute(
            select(ApInvoice).where(ApInvoice.source_invoice_id == inv_id))).scalar_one_or_none()
        if ap is not None and ap.status in ("paid", "partially_paid"):
            if remaining is None:
                ap.status, ap.paid_amount = "posted", 0
            elif remaining == "partially_paid":
                ap.status = "partially_paid"
                # execute() set paid_amount to the total only when it closed the row
                if ap.paid_amount == ap.total_amount:
                    ap.paid_amount = 0


async def _give_back_credits(db: AsyncSession, record: PaymentRecord) -> None:
    apps = (await db.execute(
        select(VendorCreditApplication).where(VendorCreditApplication.payment_record_id == record.id)
    )).scalars().all()
    for app_row in apps:
        credit = (await db.execute(
            select(VendorCredit).where(VendorCredit.id == app_row.credit_id).with_for_update()
        )).scalar_one()
        credit.applied_amount = credit.applied_amount - app_row.applied_amount
        credit.remaining_amount = credit.remaining_amount + app_row.applied_amount
        if credit.status == EXHAUSTED and credit.remaining_amount > 0:
            credit.status = AVAILABLE
        await db.delete(app_row)


async def _reopen_process_task(db: AsyncSession, pa: PaymentApplication,
                               record: PaymentRecord) -> None:
    """Re-open the process_pa task execute() closed with this payment. Only a
    task closed at or after the record was written — an older closed task
    belongs to an earlier round of approval."""
    if (await db.execute(select(sa.func.count()).select_from(Task).where(
            Task.document_type.in_(_PA_KINDS), Task.document_id == pa.id,
            Task.is_completed.is_(False)))).scalar_one():
        return
    task = (await db.execute(
        select(Task).where(Task.document_type.in_(_PA_KINDS), Task.document_id == pa.id,
                           Task.is_completed.is_(True), Task.completed_at >= record.created_at)
        .order_by(Task.completed_at.desc()).limit(1)
    )).scalar_one_or_none()
    if task is not None:
        task.is_completed, task.completed_at = False, None


async def reverse_payment_record(db: AsyncSession, record: PaymentRecord, *,
                                 reason: str, actor_name: str) -> None:
    """Undo one PA payment. Raises ValueError (409). The caller commits."""
    ev = await _payment_event(db, record)
    jv = None if ev is None else (await db.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == ev.id)
    )).scalar_one_or_none()
    await _check_reversible(db, record, ev, jv)

    pa = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id == record.pa_id).with_for_update()
    )).scalar_one()

    await _undo_invoice_writeback(db, pa, record)
    await _give_back_credits(db, record)
    if pa.status == "processed":
        pa.status = "approved"
        pa.paid_at = None
        await _reopen_process_task(db, pa, record)

    if jv is not None:
        await db.execute(sa.delete(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id))
        await db.delete(jv)
    if ev is not None:
        await db.delete(ev)  # posting_lines / dimensions: ON DELETE CASCADE

    # Read by people in the payment's notes: plant time, UI-DATE-002, with the
    # zone abbreviation because the note outlives any change to the setting.
    stamp = fmt_datetime(datetime.now(timezone.utc), with_zone=True)
    record.status = CANCELLED
    record.notes = "\n".join(filter(None, [record.notes, f"Reversed {stamp} by {actor_name}: {reason}"]))
    await db.flush()


async def reverse_batch(db: AsyncSession, batch: PaymentBatch, user: dict, reason: str) -> PaymentBatch:
    """Reverse every paid line of an executed batch, all or nothing. Raises
    ValueError (409) / PaymentPermissionError (403). The caller commits."""
    await payment_execute._check_can_pay(db, user)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to reverse a payment batch")
    if batch.status != EXECUTED:
        raise ValueError(f"Only an executed batch can be reversed (this one is {batch.status})")
    actor_name = await payment_execute._actor_name(db, uuid.UUID(user["sub"]))

    lines = (await db.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.batch_id == batch.id)
    )).scalars().all()
    for ln in lines:
        if ln.status != "paid":
            continue
        record = (await db.execute(
            select(PaymentRecord).where(PaymentRecord.id == ln.payment_record_id).with_for_update()
        )).scalar_one_or_none()
        if record is None:
            raise ValueError(f"{ln.doc_number}: its payment record is missing")
        await reverse_payment_record(db, record, reason=reason, actor_name=actor_name)
        ln.status = REVERSED

    batch.status = REVERSED
    await db.flush()
    return batch


async def unreversed_paid_lines(db: AsyncSession, batch_id: uuid.UUID) -> int:
    """Lines whose payment still stands — what makes a batch unsafe to delete."""
    return int((await db.execute(
        select(sa.func.count()).select_from(PaymentBatchLine)
        .join(PaymentRecord, PaymentRecord.id == PaymentBatchLine.payment_record_id)
        .where(PaymentBatchLine.batch_id == batch_id, PaymentRecord.status == "completed")
    )).scalar_one())
