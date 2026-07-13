"""AP accrual — post a matched vendor invoice to the spine (Phase a A2).

Called when an EPMS invoice reaches `matched` (3-way match passed): the
liability exists, so we emit the accrual half that pairs with the payment
event emitted later at PA processing:

    debit  purchase_expense   amount + non-recoverable tax   (fallback account
                              via line_role mapping; per-line precision later)
    debit  sales_tax          one line per recoverable tax line (ITC, tax_code)
                              — or the whole header tax when no tax lines exist
                              (tax_code NULL: surfaces on the A5 exception list)
    credit accounts_payable   total_amount  (partner = vendor)

Idempotent on (invoice, id, accrual) — re-matching after an exception never
double-posts.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.payment_execute import _stamp_account_codes, _stamp_fx
from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine, OPEN_STATUSES
from app.models.posting import PostingEvent, PostingLine
from app.services.posting import emit_event

_ZERO = Decimal("0")


async def has_postings(db: AsyncSession, invoice_id: uuid.UUID) -> bool:
    """True if any GL event references this AP invoice (accrual or otherwise)."""
    from sqlalchemy import func
    n = (await db.execute(
        select(func.count()).select_from(PostingEvent)
        .where(PostingEvent.source_doc_type == "ap_invoice",
               PostingEvent.source_doc_id == invoice_id)
    )).scalar_one()
    return n > 0


async def reverse_invoice_accrual(db: AsyncSession, invoice_id: uuid.UUID) -> dict:
    """Emit the mirror of this invoice's accrual (debit AP back, credit expense/ITC).

    Called when a posted AP invoice is voided — the GL must not keep carrying a
    liability whose source document is gone. Lines are copied from the original
    accrual (not recomputed) so the reversal nets to exactly zero even if the
    invoice fields changed since posting. Idempotent on
    (ap_invoice, id, accrual_reversal); no-op when no accrual exists."""
    ev = (await db.execute(
        select(PostingEvent).where(PostingEvent.source_doc_type == "ap_invoice",
                                   PostingEvent.source_doc_id == invoice_id,
                                   PostingEvent.event_type == "accrual")
    )).scalar_one_or_none()
    if ev is None:
        return {"invoice_id": invoice_id, "posting_event_id": None, "reversed": False}

    lines = (await db.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id)
        .order_by(PostingLine.line_no)
    )).scalars().all()
    reversal = [
        {"line_role": ln.line_role, "account_code": ln.account_code,
         "debit": ln.credit, "credit": ln.debit,
         "cost_center_id": ln.cost_center_id, "department_id": ln.department_id,
         "partner_id": ln.partner_id, "partner_name": ln.partner_name,
         "tax_code": ln.tax_code, "currency": ln.currency, "fx_rate": ln.fx_rate,
         "memo": f"Reversal of accrual {ev.source_doc_number}"}
        for ln in lines
    ]
    event_id = await emit_event(
        db,
        source_service="finance",
        source_doc_type="ap_invoice",
        source_doc_id=invoice_id,
        source_doc_number=ev.source_doc_number,
        event_type="accrual_reversal",
        lines=reversal,
    )
    return {"invoice_id": invoice_id, "posting_event_id": event_id,
            "reversed": event_id is not None}


async def post_invoice_accrual(db: AsyncSession, invoice_id: uuid.UUID) -> dict:
    """Raises LookupError (404) / ValueError (409). Returns event summary."""
    inv = (await db.execute(
        select(ApInvoice).where(ApInvoice.id == invoice_id)
    )).scalar_one_or_none()
    if inv is None:
        raise LookupError("AP invoice not found")
    if inv.status not in (OPEN_STATUSES + ("paid",)):
        raise ValueError(f"AP invoice in status '{inv.status}' is not accruable (needs posted)")

    tax_lines = (await db.execute(
        select(ApInvoiceTaxLine).where(ApInvoiceTaxLine.invoice_id == invoice_id)
        .order_by(ApInvoiceTaxLine.line_no)
    )).scalars().all()

    recoverable = [t for t in tax_lines if t.recoverable and t.tax_amount > _ZERO]
    recoverable_total = sum((t.tax_amount for t in recoverable), _ZERO)
    non_recoverable = inv.tax_amount - recoverable_total if tax_lines else _ZERO

    lines: list[dict] = [
        {"line_role": "purchase_expense", "debit": inv.amount + non_recoverable,
         "partner_id": inv.vendor_id, "partner_name": inv.vendor_name,
         "currency": inv.currency},
    ]
    if tax_lines:
        for t in recoverable:
            lines.append({"line_role": "sales_tax", "debit": t.tax_amount,
                          "tax_code": t.tax_code, "currency": inv.currency})
    elif inv.tax_amount > _ZERO:
        # no line-level split yet — assume recoverable, flag via NULL tax_code
        lines.append({"line_role": "sales_tax", "debit": inv.tax_amount,
                      "tax_code": None, "currency": inv.currency})
    lines.append({"line_role": "accounts_payable", "credit": inv.total_amount,
                  "partner_id": inv.vendor_id, "partner_name": inv.vendor_name,
                  "currency": inv.currency})

    event_id = await emit_event(
        db,
        source_service="finance",
        source_doc_type="ap_invoice",
        source_doc_id=inv.id,
        source_doc_number=inv.ap_invoice_number,
        event_type="accrual",
        lines=await _stamp_fx(db, await _stamp_account_codes(db, lines), inv.invoice_date),
    )
    return {
        "invoice_id": inv.id,
        "posting_event_id": event_id,          # None = already accrued (idempotent)
        "already_accrued": event_id is None,
    }
