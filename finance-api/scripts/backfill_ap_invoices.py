"""Backfill ap_invoices from existing EPMS invoices + OA expense_invoices.

All services share one DB, so this reads both source tables directly and upserts
via crud.ap_invoice.upsert (idempotent on (source, source_invoice_id)).

Run:  python -m scripts.backfill_ap_invoices
"""
import asyncio
from decimal import Decimal

from sqlalchemy import select, text

from app.crud import ap_invoice as ap_crud
from app.db.base import AsyncSessionLocal
from app.models.mirrors import Invoice, InvoiceTaxLine


def _s(v) -> str:
    return str(Decimal(str(v)).quantize(Decimal("0.01")))


def _epms_status(s: str) -> str:
    if s in ("matched", "approved"):
        return "posted"
    if s == "paid":
        return "paid"
    if s == "partially_paid":
        return "partially_paid"
    return "draft"


def _oa_status(s: str) -> str:
    return "posted" if s == "used" else "draft"


def _epms_args(inv, tax_rows):
    payload = {
        "source_ref": inv.internal_ref,
        "vendor_id": inv.vendor_id, "vendor_name": inv.vendor_name,
        "vendor_invoice_number": inv.vendor_invoice_number,
        "amount": _s(inv.amount), "tax_amount": _s(inv.tax_amount),
        "total_amount": _s(inv.total_amount), "currency": inv.currency,
        "invoice_date": inv.invoice_date, "due_date": inv.due_date,   # date objects
        "status": _epms_status(inv.status), "source_status": inv.status,
        "po_id": inv.po_id, "po_number": inv.po_number,
    }
    tax_lines = [
        {"line_no": t.line_no, "tax_code": t.tax_code,
         "taxable_base": str(getattr(t, "taxable_amount", None)) if getattr(t, "taxable_amount", None) is not None else "0",
         "tax_amount": _s(t.tax_amount), "recoverable": t.recoverable}
        for t in tax_rows
    ]
    return "epms", inv.id, payload, tax_lines


def _oa_args(row):
    inv_date = row.invoice_date or row.created_at.date()
    payload = {
        "source_ref": row.invoice_number,
        "vendor_id": row.vendor_id, "vendor_name": row.vendor_name,
        "vendor_invoice_number": row.invoice_number,
        "amount": _s(row.subtotal), "tax_amount": _s(row.tax_amount),
        "total_amount": _s(row.total_amount), "currency": row.currency,
        "invoice_date": inv_date, "due_date": row.due_date,          # date objects
        "status": _oa_status(row.status), "source_status": row.status,
        "po_id": None, "po_number": None,
    }
    return "oa", row.id, payload, []


async def run() -> None:
    async with AsyncSessionLocal() as db:
        epms_n = oa_n = 0
        invoices = (await db.execute(select(Invoice))).scalars().all()
        for inv in invoices:
            tax = (await db.execute(
                select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == inv.id)
                .order_by(InvoiceTaxLine.line_no))).scalars().all()
            source, sid, payload, tax_lines = _epms_args(inv, tax)
            await ap_crud.upsert(db, source=source, source_invoice_id=sid,
                                 payload=payload, tax_lines=tax_lines)
            epms_n += 1
        oa_rows = (await db.execute(text(
            "SELECT id, invoice_number, vendor_id, vendor_name, invoice_date, due_date,"
            " currency, subtotal, tax_amount, total_amount, status, created_at"
            " FROM expense_invoices"))).mappings().all()
        for r in oa_rows:
            row = type("Row", (), dict(r))()
            source, sid, payload, tax_lines = _oa_args(row)
            await ap_crud.upsert(db, source=source, source_invoice_id=sid,
                                 payload=payload, tax_lines=tax_lines)
            oa_n += 1
        await db.commit()
        print(f"Backfill complete: {epms_n + oa_n} ap_invoices upserted "
              f"({epms_n} epms + {oa_n} oa).")


if __name__ == "__main__":
    asyncio.run(run())
