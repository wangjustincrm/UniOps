"""Build + push a normalized AP invoice to finance ap_invoices (Plan 2).

EPMS owns the rich invoice (PO 3-way match, allocations, tax lines); finance
owns the normalized AP header. On every lifecycle change we upsert a JSON-ready
snapshot into finance (fail-open via finance_client)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.invoice_tax_line import InvoiceTaxLine
from app.services import finance_client


def _ap_status(invoice_status: str, void: bool) -> str:
    if void:
        return "void"
    if invoice_status in ("matched", "approved"):
        return "posted"
    if invoice_status == "paid":
        return "paid"
    return "draft"   # unmatched / exception / anything else


async def sync_ap_invoice(db: AsyncSession, invoice: Invoice, bearer_token: str,
                          *, void: bool = False) -> None:
    """Upsert this EPMS invoice's normalized header+tax into finance. Fail-open."""
    tax_rows = (await db.execute(
        select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == invoice.id)
        .order_by(InvoiceTaxLine.line_no)
    )).scalars().all()

    payload = {
        "source": "epms",
        "source_invoice_id": str(invoice.id),
        "source_ref": invoice.internal_ref,
        "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
        "vendor_name": invoice.vendor_name,
        "vendor_invoice_number": invoice.vendor_invoice_number,
        "amount": str(invoice.amount),
        "tax_amount": str(invoice.tax_amount),
        "total_amount": str(invoice.total_amount),
        "currency": invoice.currency,
        "invoice_date": invoice.invoice_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": _ap_status(invoice.status, void),
        "source_status": invoice.status,
        "po_id": str(invoice.po_id) if invoice.po_id else None,
        "po_number": invoice.po_number,
        "tax_lines": [
            {"line_no": t.line_no, "tax_code": t.tax_code,
             "taxable_base": str(t.taxable_amount) if t.taxable_amount is not None else "0",
             "tax_amount": str(t.tax_amount), "recoverable": t.recoverable}
            for t in tax_rows
        ],
    }
    await finance_client.upsert_ap_invoice(payload=payload, bearer_token=bearer_token)
