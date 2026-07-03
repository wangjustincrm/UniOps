"""One-time fix: dedupe imported invoices that violate Invoice-No uniqueness.

The legacy PMS entered one INVOICE row per PO, so a single vendor invoice split
across several POs was imported as multiple invoice records sharing the same
vendor_invoice_number — a violation of our "Invoice No is unique per vendor" rule.

This merges each (vendor_id, vendor_invoice_number) group into ONE invoice:
  * multi-PO groups → the keeper gains one synthetic invoice line + one
    invoice_po_allocation per PO, so the detail page renders "Purchase Orders (N)".
  * same-PO groups  → pure duplicates simply collapse into the keeper.

Before the duplicates are deleted, their dependents are repointed to the keeper:
  invoice_attachments, invoice_tax_lines, and payment_applications.invoice_ids.

Keeper = the group member with the smallest INV-<n> ref (matches the importer's
INV-<min iid> keying). Amounts are summed per distinct PO (max within a PO so
accidental same-PO duplicate rows don't double-count). Status becomes 'paid' only
when every member was paid.

Safe by default (dry-run). Pass --commit to write. Idempotent: a second run finds
no groups >1 and does nothing.

Usage (from the epms-api dir / inside the epms-api container):
    python -m scripts.dedup_invoices              # dry-run
    python -m scripts.dedup_invoices --commit     # write to the configured DB
    python -m scripts.dedup_invoices --db-url postgresql+asyncpg://u:p@host/db
"""
from __future__ import annotations

import argparse
import asyncio
import re
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.schemas.invoice import InvoiceLineItem

_REF = re.compile(r"INV-(\d+)")

# Hosts that must never be touched without an explicit --allow-production.
PRODUCTION_HOSTS = ("10.10.50.20",)


def _iid(ref: str | None) -> int:
    m = _REF.match(ref or "")
    return int(m.group(1)) if m else (1 << 62)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def run(dry_run: bool, db_url: str | None = None, allow_production: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}). This targets the "
            f"shared prod database. Re-run with --allow-production if that is "
            f"truly intended. (Tip: run inside the epms-api container or pass "
            f"--db-url for the local DB to test safely.)"
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sf() as db:
            stats = await dedup_invoices_pass(db)
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print_dedup_stats(stats, dry_run)
    return stats


def print_dedup_stats(stats: dict, dry_run: bool) -> None:
    print(
        f"Invoice dedup ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  duplicate groups:      {stats.get('dup_groups', 0)}\n"
        f"    of which multi-PO:   {stats.get('multi_po_groups', 0)}\n"
        f"  invoices deleted:      {stats.get('deleted', 0)}\n"
        f"  allocations created:   {stats.get('allocations', 0)}\n"
        f"  PAs relinked:          {stats.get('pa_relinked', 0)}\n"
        f"  attachments deduped:   {stats.get('attachments_deduped', 0)}"
    )


async def dedup_invoices_pass(db) -> dict:
    """Merge duplicate invoices + dedup their attachments on the given session.

    Does NOT commit — the caller owns the transaction (so this can run both as a
    standalone script and as a pass inside the PMS importer's run_load). Returns
    a stats dict. Idempotent: with no duplicates it only dedups attachments.
    """
    stats: dict[str, int] = defaultdict(int)
    invoices = (await db.execute(select(Invoice))).scalars().all()
    groups: dict[tuple, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        groups[(inv.vendor_id, inv.vendor_invoice_number)].append(inv)

    remap: dict[str, str] = {}          # deleted invoice uuid → keeper uuid
    del_ids: list[uuid.UUID] = []

    for members in groups.values():
        if len(members) < 2:
            continue
        stats["dup_groups"] += 1
        members.sort(key=lambda x: (_iid(x.internal_ref), str(x.id)))
        keeper, dups = members[0], members[1:]

        # Aggregate per distinct PO (max within a PO → no double-count).
        po_amt: dict[uuid.UUID, Decimal] = {}
        po_num: dict[uuid.UUID, str | None] = {}
        po_order: list[uuid.UUID] = []
        no_po = Decimal("0")
        paid_all = True
        for m in members:
            if m.status != "paid":
                paid_all = False
            if m.po_id is not None:
                if m.po_id not in po_amt:
                    po_order.append(m.po_id)
                    po_num[m.po_id] = m.po_number
                po_amt[m.po_id] = max(po_amt.get(m.po_id, Decimal("0")), m.amount or Decimal("0"))
            else:
                no_po = max(no_po, m.amount or Decimal("0"))

        total = sum(po_amt.values(), Decimal("0")) + no_po
        keeper.amount = total
        keeper.total_amount = total + (keeper.tax_amount or Decimal("0"))
        keeper.status = "paid" if paid_all else "matched"
        if keeper.po_id not in po_amt and po_order:
            keeper.po_id = po_order[0]
            keeper.po_number = po_num[po_order[0]]

        # Multi-PO → rebuild keeper's lines + allocations (idempotent).
        if len(po_order) > 1:
            stats["multi_po_groups"] += 1
            await db.execute(sa_delete(InvoicePoAllocation).where(
                InvoicePoAllocation.invoice_id == keeper.id))
            lines: list = []
            for pid in po_order:
                amt = po_amt[pid]
                line = InvoiceLineItem(
                    id=uuid.uuid4(),
                    description=(f"PO {po_num[pid] or pid}")[:500],
                    quantity=Decimal("1"), unit_price=amt, line_total=amt,
                )
                lines.append(line.model_dump(mode="json"))
                db.add(InvoicePoAllocation(
                    invoice_id=keeper.id, invoice_line_id=line.id,
                    po_id=pid, po_line_id=None,
                    allocated_amount=amt, allocated_tax=Decimal("0"),
                    allocated_total=amt,
                ))
                stats["allocations"] += 1
            keeper.line_items = lines
            flag_modified(keeper, "line_items")

        # Repoint dependents of each duplicate to the keeper, then drop it.
        for d in dups:
            for tbl in ("invoice_attachments", "invoice_tax_lines"):
                await db.execute(
                    text(f"UPDATE {tbl} SET invoice_id = :k WHERE invoice_id = :d")
                    .bindparams(k=keeper.id, d=d.id))
            remap[str(d.id)] = str(keeper.id)
            del_ids.append(d.id)
            stats["deleted"] += 1

    # Rewrite PA.invoice_ids that pointed at a now-merged duplicate.
    if remap:
        pas = (await db.execute(
            select(PaymentApplication).where(PaymentApplication.invoice_ids.isnot(None)))
        ).scalars().all()
        for pa in pas:
            ids = pa.invoice_ids or []
            new: list[str] = []
            seen: set[str] = set()
            changed = False
            for x in ids:
                nx = remap.get(x, x)
                changed = changed or (nx != x)
                if nx not in seen:
                    seen.add(nx)
                    new.append(nx)
            if changed or len(new) != len(ids):
                pa.invoice_ids = new
                flag_modified(pa, "invoice_ids")
                stats["pa_relinked"] += 1

    if del_ids:
        await db.execute(sa_delete(Invoice).where(Invoice.id.in_(del_ids)))

    # Dedup attachments: merged invoices carry the same physical file once per
    # former duplicate (identical file_name + size, but a distinct storage_key/blob
    # per import). Keep the earliest row per (invoice_id, file_name, file_size_bytes);
    # drop the rest. Runs over the whole table so a re-run cleans stragglers too.
    res_att = await db.execute(text(
        "DELETE FROM invoice_attachments a USING ("
        "  SELECT id, row_number() OVER ("
        "    PARTITION BY invoice_id, file_name, file_size_bytes"
        "    ORDER BY uploaded_at, id) rn"
        "  FROM invoice_attachments) d "
        "WHERE a.id = d.id AND d.rn > 1"
    ))
    stats["attachments_deduped"] = res_att.rowcount or 0

    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.dedup_invoices")
    p.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p.add_argument("--db-url", help="explicit async DB URL (overrides settings)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    args = p.parse_args(argv)
    asyncio.run(run(dry_run=not args.commit, db_url=args.db_url,
                    allow_production=args.allow_production))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
