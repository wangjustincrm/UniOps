"""One-off maintenance: clear ALL Accounts Payable invoices + reverse their GL.

Scope (confirmed with user 2026-06-30):
  * DELETE every row from `ap_invoices` (cascades `ap_invoice_tax_lines`).
  * Reverse the GL accruals by DELETING posting events keyed
    source_doc_type='ap_invoice' (cascades posting_lines + line dimensions).
  * For invoices that were PAID, also DELETE the matching PA payment posting
    event (source_doc_type pa/pa_dir, event_type='payment') on the PA whose
    invoice_ids contains the AP invoice's source_invoice_id — so the AP control
    account nets to zero. Each payment event is a balanced (debit AP / credit
    bank) pair, so removing it keeps the GL balanced overall.

Leaves untouched (reported as residual): payment_records rows and the PA
documents themselves (status stays 'processed'). Those are the PA module, out
of the AP-List scope; flagged so the decision is explicit.

Usage:
    python -m scripts.clear_ap_invoices            # count-only (read, no writes)
    python -m scripts.clear_ap_invoices --execute  # delete inside one txn + verify
"""
import asyncio
import sys

from sqlalchemy import text

from app.db.base import AsyncSessionLocal

AP_EVENTS = "source_doc_type = 'ap_invoice'"


async def paid_pa_payment_event_ids(db) -> list[str]:
    """posting_event ids for PA payments tied to PAID/partially_paid ap_invoices."""
    rows = (await db.execute(text("""
        select distinct pe.id
        from ap_invoices ai
        join payment_applications pa
          on pa.invoice_ids @> to_jsonb(ai.source_invoice_id::text)
        join posting_events pe
          on pe.source_doc_id = pa.id
         and pe.source_doc_type in ('pa', 'pa_dir')
         and pe.event_type = 'payment'
        where ai.status in ('paid', 'partially_paid')
    """))).fetchall()
    return [str(r[0]) for r in rows]


async def counts(db, pay_event_ids: list[str]) -> dict:
    out = {}
    out["ap_invoices"] = (await db.execute(text("select count(*) from ap_invoices"))).scalar()
    out["ap_invoice_tax_lines"] = (await db.execute(text("select count(*) from ap_invoice_tax_lines"))).scalar()
    out["accrual_events"] = (await db.execute(
        text(f"select count(*) from posting_events where {AP_EVENTS}"))).scalar()
    out["paid_pa_payment_events"] = len(pay_event_ids)
    return out


async def report(db, pay_event_ids: list[str]) -> None:
    print("== ap_invoices by source / status ==")
    for row in (await db.execute(text(
        "select source, status, count(*), coalesce(sum(total_amount),0), coalesce(sum(paid_amount),0) "
        "from ap_invoices group by source, status order by 1,2"
    ))).fetchall():
        print(f"  {row[0]:<5} {row[1]:<16} n={row[2]:<4} total={row[3]} paid={row[4]}")

    print("\n== AP accrual events by fiscal_period ==")
    for row in (await db.execute(text(
        f"select fiscal_period, count(*) from posting_events where {AP_EVENTS} "
        "group by fiscal_period order by 1"
    ))).fetchall():
        print(f"  {row[0]}: {row[1]} events")

    print("\n== PA payment events to delete (paid AP invoices) ==")
    if pay_event_ids:
        for row in (await db.execute(text("""
            select pe.source_doc_number, pe.fiscal_period,
                   coalesce(sum(pl.debit),0), coalesce(sum(pl.credit),0)
            from posting_events pe join posting_lines pl on pl.event_id = pe.id
            where pe.id = any(:ids) group by pe.source_doc_number, pe.fiscal_period
            order by 1
        """), {"ids": pay_event_ids})).fetchall():
            print(f"  PA {row[0]} ({row[1]})  debit={row[2]} credit={row[3]}")
    else:
        print("  (none)")

    # residual that this script intentionally does NOT delete
    print("\n  NOTE: payment_records rows and PA 'processed' status are left as-is (PA module).")


async def main(execute: bool) -> None:
    async with AsyncSessionLocal() as db:
        pay_ids = await paid_pa_payment_event_ids(db)
        before = await counts(db, pay_ids)
        print("BEFORE:", before, "\n")
        await report(db, pay_ids)

        if not execute:
            print("\n[count-only] no changes made. Re-run with --execute to delete.")
            return

        print("\n[execute] deleting inside one transaction ...")
        pay_deleted = 0
        if pay_ids:
            pay_deleted = (await db.execute(
                text("delete from posting_events where id = any(:ids)"),
                {"ids": pay_ids})).rowcount
        ev = (await db.execute(text(f"delete from posting_events where {AP_EVENTS}"))).rowcount
        inv = (await db.execute(text("delete from ap_invoices"))).rowcount
        print(f"  deleted PA payment events: {pay_deleted}")
        print(f"  deleted AP accrual events: {ev}")
        print(f"  deleted ap_invoices:       {inv}")

        after = await counts(db, await paid_pa_payment_event_ids(db))
        print("AFTER (pre-commit):", after)
        residual = {k: v for k, v in after.items() if v}
        if residual:
            await db.rollback()
            raise SystemExit(f"ABORT: residual rows {residual} — rolled back, nothing committed.")

        await db.commit()
        print("COMMITTED. AP list cleared, accruals + paid-invoice payments reversed.")


if __name__ == "__main__":
    asyncio.run(main(execute="--execute" in sys.argv))
