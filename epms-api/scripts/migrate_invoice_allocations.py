"""Backfill invoice_po_allocations for existing single-PO invoices.

Idempotent: skips invoices that already have allocations. Also assigns stable
ids to JSONB line items that lack one.

Run:  python -m scripts.migrate_invoice_allocations
"""
import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from app.db.session import AsyncSessionLocal
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation


async def run() -> None:
    async with AsyncSessionLocal() as db:
        invoices = (await db.execute(select(Invoice))).scalars().all()
        created = 0
        for inv in invoices:
            changed = False
            items = list(inv.line_items or [])
            for it in items:
                if not it.get("id"):
                    it["id"] = str(uuid.uuid4())
                    changed = True
            if changed:
                inv.line_items = items
                # JSONB is a plain (non-Mutable) column and the inner dicts are
                # the same objects SQLAlchemy snapshotted on load, so in-place
                # mutation produces a no-op diff at flush. Force the UPDATE.
                flag_modified(inv, "line_items")

            if inv.po_id is None:
                continue
            existing = (await db.execute(
                select(func.count()).select_from(InvoicePoAllocation)
                .where(InvoicePoAllocation.invoice_id == inv.id)
            )).scalar_one()
            if existing:
                continue

            line_id = items[0]["id"] if items else str(uuid.uuid4())
            db.add(InvoicePoAllocation(
                invoice_id=inv.id,
                invoice_line_id=uuid.UUID(line_id),
                po_id=inv.po_id,
                po_line_id=None,
                allocated_amount=inv.amount,
                allocated_tax=inv.tax_amount,
                allocated_total=inv.total_amount,
                variance=inv.variance,
                variance_pct=inv.variance_pct,
                note="Backfilled from single-PO invoice",
            ))
            created += 1
        await db.commit()
        print(f"Backfill complete: {created} allocations created.")


if __name__ == "__main__":
    asyncio.run(run())
