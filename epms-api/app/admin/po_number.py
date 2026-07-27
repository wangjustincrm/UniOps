"""PO number regeneration + cascade rename across denormalized copies.

PO number format: PO-{vendor_code}-{YYMM}-{seq:02d} (see crud/po._next_number).
Changing the vendor makes the vendor_code segment stale; regeneration mints a new
number under the new vendor's code and renames every denormalized copy joined by
po_id (reliable). finance ap_invoices has no po_id FK → best-effort string match."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.approval import ApprovalEvent


async def _next_number(db: AsyncSession, vendor_code: str) -> str:
    ym = datetime.now(timezone.utc).strftime("%y%m")
    prefix = f"PO-{vendor_code}-{ym}-"
    return await next_number(db, PurchaseOrder.number, prefix, width=2)


async def regenerate_and_cascade(db: AsyncSession, po, vendor_code: str) -> dict[str, int | str]:
    """Mint a new number for `po` under vendor_code, cascade-rename all denormalized
    copies. Caller commits. Returns a summary for the audit cascade_summary."""
    old = po.number
    new = await _next_number(db, vendor_code)
    summary: dict[str, int | str] = {"old_number": old, "new_number": new}

    po.number = new

    async def _bump(model, col):
        res = await db.execute(update(model).where(model.po_id == po.id).values(**{col: new}))
        return res.rowcount or 0

    summary["purchase_requests"] = (await db.execute(
        update(PurchaseRequest).where(PurchaseRequest.po_id == po.id)
        .values(po_number=new))).rowcount or 0
    summary["payment_applications"] = await _bump(PaymentApplication, "po_number")
    summary["goods_receipts"] = await _bump(GoodsReceipt, "po_number")
    summary["invoices"] = await _bump(Invoice, "po_number")
    summary["tasks"] = (await db.execute(
        update(Task).where(Task.document_id == po.id, Task.document_type == "po")
        .values(document_number=new))).rowcount or 0
    summary["approval_events"] = (await db.execute(
        update(ApprovalEvent).where(ApprovalEvent.document_id == po.id,
                                    ApprovalEvent.document_type == "po")
        .values(document_number=new))).rowcount or 0

    # finance ap_invoices: no po_id FK on that mirror → best-effort string match.
    # Guard on table existence (absent in the epms test DB).
    exists = (await db.execute(text("SELECT to_regclass('ap_invoices')"))).scalar_one()
    if exists is not None:
        res = await db.execute(
            text("UPDATE ap_invoices SET po_number = :new WHERE po_number = :old"),
            {"new": new, "old": old})
        summary["ap_invoices_by_string"] = res.rowcount or 0

    return summary
