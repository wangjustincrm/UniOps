"""Build + push a normalized AP invoice to finance ap_invoices (Plan 3, OA side).

OA (expense-api) owns the OCR'd vendor invoice; finance owns the normalized AP
header. On lifecycle changes we upsert a JSON-ready snapshot into finance
(fail-open via finance_client). OA has only header tax (no tax_code lines)."""
from app.services import finance_client


def _ap_status(invoice_status: str, void: bool) -> str:
    if void:
        return "void"
    if invoice_status == "used":
        return "posted"
    return "draft"   # reviewed / uploaded / anything else


async def sync_ap_invoice(db, invoice, bearer_token: str, *, void: bool = False) -> None:
    """Upsert this OA expense_invoice's normalized header into finance. Fail-open.
    `db` is accepted for signature symmetry with the EPMS sync but unused (OA has
    no tax_code lines to load)."""
    inv_date = invoice.invoice_date or invoice.created_at.date()
    payload = {
        "source": "oa",
        "source_invoice_id": str(invoice.id),
        "source_ref": invoice.invoice_number,
        "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
        "vendor_name": invoice.vendor_name,
        "vendor_invoice_number": invoice.invoice_number,
        "amount": str(invoice.subtotal),
        "tax_amount": str(invoice.tax_amount),
        "total_amount": str(invoice.total_amount),
        "currency": invoice.currency,
        "invoice_date": inv_date.isoformat(),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": _ap_status(invoice.status, void),
        "source_status": invoice.status,
        "po_id": None,
        "po_number": None,
        "tax_lines": [],
    }
    await finance_client.upsert_ap_invoice(payload=payload, bearer_token=bearer_token)
