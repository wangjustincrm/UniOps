"""Reflect QBO-already-paid PAs as 'processed' in EPMS WITHOUT re-booking payment.

These `approved` (un-processed) PAs were actually paid in QuickBooks. QBO is the
accounting system of record and its EPMS mirror (qbo_* tables) is READ-ONLY /
non-posting — QBO already holds the authoritative BillPayment + GL entry. Running
these through the normal payment executor (POST /finance/v1/payments/execute)
would emit a DUPLICATE posting_event + journal voucher + payment_record in EPMS's
OWN ledger for money QBO already booked. So this script only REFLECTS the paid
state and deliberately writes NO posting_events / journal_vouchers / payment_records.

Source of truth: scripts/data/qbo_reconcile_map.json — each approved PA matched to
QBO Bill(s) by vendor-invoice-number = doc_number with tax-aware totals and QBO
balance=0 (fully paid); `paid_date` = the QBO bill-payment txn_date.

Per PA (idempotent; only touches PAs currently 'approved'):
  - status  -> 'processed'
  - paid_at -> the QBO payment date (midnight UTC)  [dashboards key on paid_at]
  - linked invoices in matched/approved/partially_paid -> 'paid'
      (prepayment PA -> 'partially_paid', mirroring the executor)
  - finance ap_invoices in posted/partially_paid -> 'paid' + paid_amount (raw SQL)
  - open pa/pa_dir approval tasks -> completed
Mirrors finance-api payment_execute.execute() MINUS the bank line, payment_record
and emit_event/posting — i.e. reflects payment without re-booking it.

Dry-run by DEFAULT (rolls back). Pass --apply to commit. Refuses ENVIRONMENT=
production unless --allow-production.

Run (app server; script not in the deployed image -> mount it):
  cd /opt/uniops && git fetch origin && \
  git checkout origin/fix/reconcile-qbo-paid-pas -- \
    epms-api/scripts/mark_qbo_paid_processed.py epms-api/scripts/data/qbo_reconcile_map.json
  docker compose -f docker-compose.prod.yml run --rm --no-deps \
    -v /opt/uniops/epms-api/scripts:/app/scripts epms-api \
    python -m scripts.mark_qbo_paid_processed --allow-production          # dry-run
  ... python -m scripts.mark_qbo_paid_processed --apply --allow-production  # commit
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.task import Task

MAP_PATH = Path(__file__).parent / "data" / "qbo_reconcile_map.json"


async def _ap_invoices_present(db) -> bool:
    return (await db.execute(text("SELECT to_regclass('public.ap_invoices')"))).scalar() is not None


async def reflect_one(db, pa_number: str, paid_date: str, ap_present: bool) -> str:
    """Reflect one PA as processed. Idempotent; only acts on 'approved' PAs.
    Writes NO posting_events / journal_vouchers / payment_records."""
    pa = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.pa_number == pa_number)
    )).scalar_one_or_none()
    if pa is None:
        return "missing"
    if pa.status == "processed":
        return "already_processed"
    if pa.status != "approved":
        return f"skip_status:{pa.status}"

    y, m, d = (int(x) for x in paid_date.split("-"))
    pa.status = "processed"
    pa.paid_at = datetime(y, m, d, tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    open_tasks = (await db.execute(select(Task).where(
        Task.document_type.in_(["pa", "pa_dir"]),
        Task.document_id == pa.id,
        Task.is_completed.is_(False),
    ))).scalars().all()
    for t in open_tasks:
        t.is_completed = True
        t.completed_at = now

    inv_status = "partially_paid" if pa.pa_type == "prepayment" else "paid"
    for raw in (pa.invoice_ids or []):
        try:
            inv_id = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            continue
        inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one_or_none()
        if inv and inv.status in ("matched", "approved", "partially_paid"):
            inv.status = inv_status
        if ap_present:
            if inv_status == "paid":
                await db.execute(text(
                    "UPDATE ap_invoices SET status='paid', paid_amount=total_amount "
                    "WHERE source_invoice_id = :sid AND status IN ('posted','partially_paid')"),
                    {"sid": str(inv_id)})
            else:
                await db.execute(text(
                    "UPDATE ap_invoices SET status='partially_paid' "
                    "WHERE source_invoice_id = :sid AND status IN ('posted','partially_paid')"),
                    {"sid": str(inv_id)})

    await db.flush()
    return "processed"


async def main(apply: bool):
    data = json.loads(MAP_PATH.read_text())
    counts: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        ap_present = await _ap_invoices_present(db)
        for pa_number, spec in data.get("pa", {}).items():
            try:
                async with db.begin_nested():
                    outcome = await reflect_one(db, pa_number, spec["paid_date"], ap_present)
            except Exception as exc:
                outcome = f"error:{type(exc).__name__}"
                print(f"  ! {pa_number}: {type(exc).__name__}: {exc}")
            else:
                if outcome == "processed":
                    print(f"  processed  {pa_number}  paid_at={spec['paid_date']}  ({spec.get('confidence')})")
                elif outcome != "already_processed":
                    print(f"  {outcome:>16}  {pa_number}")
            counts[outcome] = counts.get(outcome, 0) + 1

        print("\n=== SUMMARY (ap_invoices table present: %s) ===" % ap_present)
        for k in sorted(counts):
            print(f"  {counts[k]:>4}  {k}")
        print("  NOTE: NO posting_events / journal_vouchers / payment_records written "
              "(QBO already booked these payments).")

        if apply:
            await db.commit()
            print("\nAPPLIED (committed).")
        else:
            await db.rollback()
            print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit running when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.environ.get("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        print("Refusing to run against ENVIRONMENT=production without --allow-production.",
              file=sys.stderr)
        sys.exit(2)

    asyncio.run(main(apply=args.apply))
