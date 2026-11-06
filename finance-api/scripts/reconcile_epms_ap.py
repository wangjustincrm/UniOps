"""Bring Finance AP back in step with EPMS invoices — dry run unless --apply.

Two kinds of drift, both found by query (no invoice list is hard-coded):

1. Orphans — an AP row (source epms, not void) whose EPMS invoice no longer
   exists. Data Maintenance deleted invoices without telling Finance (fixed in
   epms-api's finance_sync: a delete now voids in Finance or does not happen).
   Each is voided by the same rule as POST /ap/invoices status=void
   (crud.ap_invoice.void_for_deleted_source): never-posted → removed;
   posted → void, accrual reversed.

2. Missing — an EPMS invoice (native INV-YYYY-NNNN reference; PMS-migration
   rows were never meant to have AP rows) with no AP row. Usually its sync was
   refused because an orphan held the same vendor + invoice number. Each is
   rebuilt the way the normal flow would have built it: upserted from the EPMS
   row; if it was matched/approved/paid it is posted and accrued (as at match),
   and if it was paid, the paying PA's writeback is replayed (as at payment)
   — crud.payment_execute.apply_pa_invoice_writeback, idempotent.

Orphans are handled first, so the vendor + number they held is free again.
Every item runs in its own savepoint: one that still clashes (e.g. two genuine
invoices sharing one number) is reported and skipped, not fatal.

A dry run executes all of it and rolls back, so what it prints is what
--apply would do.

Run (finance-api image):
    python -m scripts.reconcile_epms_ap            # dry run
    python -m scripts.reconcile_epms_ap --apply
"""
import argparse
import asyncio
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.crud import ap_invoice as ap_crud
from app.crud.ap_accrual import post_invoice_accrual
from app.crud.payment_execute import apply_pa_invoice_writeback
from app.db.base import AsyncSessionLocal
from app.models.ap_invoice import POSTED, ApInvoice
from app.models.mirrors import Invoice, InvoiceTaxLine
from app.models.pa import PaymentApplication
from scripts.backfill_ap_invoices import _epms_args

NATIVE_REF = r"^INV-[0-9]{4}-[0-9]+$"
ACCRUED_SOURCE_STATUSES = ("matched", "approved", "paid", "partially_paid")


async def find_orphans(db) -> list[ApInvoice]:
    return list((await db.execute(
        select(ApInvoice).where(
            ApInvoice.source == "epms", ApInvoice.status != "void",
            ~select(Invoice.id).where(Invoice.id == ApInvoice.source_invoice_id).exists(),
        ).order_by(ApInvoice.created_at)
    )).scalars().all())


async def find_missing(db) -> list[Invoice]:
    return list((await db.execute(
        select(Invoice).where(
            Invoice.internal_ref.op("~")(NATIVE_REF),
            ~select(ApInvoice.id).where(ApInvoice.source == "epms",
                                        ApInvoice.source_invoice_id == Invoice.id).exists(),
        ).order_by(Invoice.created_at)
    )).scalars().all())


async def paying_pas(db, invoice_id: uuid.UUID) -> list[PaymentApplication]:
    ids = (await db.execute(text(
        "SELECT id FROM payment_applications WHERE status IN ('processed', 'paid') "
        "AND invoice_ids @> jsonb_build_array(CAST(:i AS text))"), {"i": str(invoice_id)}
    )).scalars().all()
    if not ids:
        return []
    return list((await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(ids))
    )).scalars().all())


async def rebuild(db, inv: Invoice) -> str:
    tax = (await db.execute(
        select(InvoiceTaxLine).where(InvoiceTaxLine.invoice_id == inv.id)
        .order_by(InvoiceTaxLine.line_no))).scalars().all()
    source, sid, payload, tax_lines = _epms_args(inv, tax)
    accrue = inv.status in ACCRUED_SOURCE_STATUSES
    if accrue:
        payload["status"] = POSTED          # as at match; payment moves it on below
    ap = await ap_crud.upsert(db, source=source, source_invoice_id=sid,
                              payload=payload, tax_lines=tax_lines)
    if not accrue:
        return f"created {ap.status}"
    await post_invoice_accrual(db, ap.id)
    note = "created posted + accrual"
    if inv.status in ("paid", "partially_paid"):
        pas = await paying_pas(db, inv.id)
        for pa in pas:
            await apply_pa_invoice_writeback(db, pa)
        await db.flush()
        await db.refresh(ap)
        note += (f", payment replayed from {', '.join(p.pa_number for p in pas)} → {ap.status}"
                 if pas else " — NO paid PA found, left posted (check by hand)")
    return note


async def reconcile(db) -> list[tuple[str, str, str]]:
    """Do it in `db` without committing. Returns (kind, label, outcome) rows;
    outcome starts with SKIPPED/FAILED when a person must look."""
    report: list[tuple[str, str, str]] = []
    for ap in await find_orphans(db):
        label = (f"{ap.source_ref} {ap.vendor_name} #{ap.vendor_invoice_number} "
                 f"{ap.total_amount} {ap.status}")
        sp = await db.begin_nested()
        try:
            outcome = await ap_crud.void_for_deleted_source(db, ap)
            await sp.commit()
        except Exception as e:                       # report, keep going
            await sp.rollback()
            outcome = f"FAILED {type(e).__name__}: {e}"
        report.append(("orphan", label, outcome))

    for inv in await find_missing(db):
        label = (f"{inv.internal_ref} {inv.vendor_name} #{inv.vendor_invoice_number} "
                 f"{inv.total_amount} {inv.status}")
        sp = await db.begin_nested()
        try:
            outcome = await rebuild(db, inv)
            await sp.commit()
        except IntegrityError as e:
            await sp.rollback()
            outcome = "SKIPPED — " + ("vendor + invoice # already on another AP row"
                                      if "uq_ap_invoices_vendor_invno" in str(e.orig) else str(e.orig))
        except Exception as e:
            await sp.rollback()
            outcome = f"FAILED {type(e).__name__}: {e}"
        report.append(("missing", label, outcome))
    return report


async def run(apply: bool) -> int:
    async with AsyncSessionLocal() as db:
        report = await reconcile(db)
        for kind in ("orphan", "missing"):
            rows = [r for r in report if r[0] == kind]
            print(f"== {kind}: {len(rows)}" + (
                " AP row(s) whose EPMS invoice is gone" if kind == "orphan"
                else " EPMS invoice(s) with no AP row"))
            for _, label, outcome in rows:
                print(f"   {label}: {outcome}")
        if apply:
            await db.commit()
            print("== Applied.")
        else:
            await db.rollback()
            print("== Dry run — nothing written. Re-run with --apply.")
    return sum(1 for r in report if r[2].startswith(("SKIPPED", "FAILED")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()
    problems = asyncio.run(run(args.apply))
    print(f"== {problems} item(s) need a person." if problems else "== Nothing left over.")


if __name__ == "__main__":
    main()
