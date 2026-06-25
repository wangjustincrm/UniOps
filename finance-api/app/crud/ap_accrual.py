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
from app.services.posting import emit_event

_ZERO = Decimal("0")


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
