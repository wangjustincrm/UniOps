"""Rebuild payment_applications.invoice_ids after an invoice re-import.

PA.invoice_ids is only populated when a PA is INSERTED; re-importing invoices
(new UUIDs) while keeping existing PAs leaves those links pointing at deleted
invoices. This pass rebuilds them from the SharePoint linkage:

    Payment Item.Title = PA number, Payment Item.InvoiceID = SharePoint invoice id
    INVOICE.Title      = vendor_invoice_number

For each PA it collects the vendor_invoice_numbers of the invoices its PA-items
reference, then matches them to the freshly-imported EPMS invoice by
(vendor_id, vendor_invoice_number) — the invoice's unique key after dedup.
Invoices that were discarded on import (no PO link) simply don't match and are
reported as unresolved.

Safe by default (dry-run). Idempotent.

Usage (inside the epms-api container, AFTER re-importing invoices):
    python -m scripts.rebuild_pa_invoice_links                          # dry-run
    python -m scripts.rebuild_pa_invoice_links --commit --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

import app.models  # noqa: F401
from app.core.config import settings
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from scripts.import_pms.extract import extract, load_staging

PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


def _staged_maps(skip_extract: bool) -> tuple[dict[int, str], dict[str, list[int]]]:
    """Return (sp_invoice_id → vendor_invoice_number, pa_number → [sp_invoice_id])."""
    if not skip_extract:
        print("Staging FULL invoice + pa_item from SharePoint ...", flush=True)
        extract(only={"invoice", "pa_item"}, since=None, skip_attachments=True)

    inv_no: dict[int, str] = {}
    for r in load_staging("invoice.json"):
        iid = r.get("ID")
        if iid is not None:
            inv_no[int(iid)] = str(r.get("Title") or "").strip()

    pa_to_iids: dict[str, list[int]] = defaultdict(list)
    for it in load_staging("pa_item.json"):
        pa_no = str(it.get("Title") or "").strip()
        iid = it.get("InvoiceID")
        if pa_no and iid:
            pa_to_iids[pa_no].append(int(iid))
    return inv_no, pa_to_iids


async def run(dry_run: bool, db_url: str | None = None,
              allow_production: bool = False, skip_extract: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    inv_no, pa_to_iids = _staged_maps(skip_extract)
    print(f"  staged: {len(inv_no)} invoices, {len(pa_to_iids)} PAs with item links")

    stats: dict[str, int] = defaultdict(int)
    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            # (vendor_id, vendor_invoice_number) → invoice.id
            inv_by_key: dict[tuple, str] = {}
            for iid, vid, vno in (await db.execute(
                select(Invoice.id, Invoice.vendor_id, Invoice.vendor_invoice_number)
            )).all():
                inv_by_key[(str(vid), vno)] = str(iid)

            pas = (await db.execute(select(PaymentApplication))).scalars().all()
            for pa in pas:
                want = pa_to_iids.get(pa.pa_number or "")
                if not want:
                    continue
                new_ids: list[str] = []
                for sp_iid in want:
                    vno = inv_no.get(sp_iid)
                    if not vno:
                        continue
                    inv_id = inv_by_key.get((str(pa.vendor_id), vno))
                    if inv_id and inv_id not in new_ids:
                        new_ids.append(inv_id)
                    elif not inv_id:
                        stats["links_unresolved"] += 1
                # Only ever ADD resolved links; never overwrite with an empty
                # list (a partial/stale extract must not wipe good links — and in
                # the intended clear→reimport→rebuild flow invoice_ids is already
                # []). PAs whose invoices were all discarded stay [].
                if new_ids and new_ids != (pa.invoice_ids or []):
                    orig_updated = pa.updated_at
                    pa.invoice_ids = new_ids
                    flag_modified(pa, "invoice_ids")
                    # Preserve updated_at — otherwise onupdate=now() bumps it and
                    # corrupts time-based dashboards ("Paid This Month") and the
                    # incremental-sync conflict check. flag_modified forces the
                    # original value into the UPDATE (a plain re-assign is a no-op).
                    pa.updated_at = orig_updated
                    flag_modified(pa, "updated_at")
                    stats["pas_updated"] += 1
                    stats["links_set"] += len(new_ids)

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"\nRebuild PA→invoice links ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  PAs updated:        {stats['pas_updated']}\n"
        f"  invoice links set:  {stats['links_set']}\n"
        f"  links unresolved:   {stats['links_unresolved']}  (invoice discarded / not imported)"
    )
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.rebuild_pa_invoice_links")
    p.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p.add_argument("--db-url", help="explicit async DB URL (overrides settings)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    p.add_argument("--skip-extract", action="store_true",
                   help="reuse already-staged invoice/pa_item instead of re-pulling")
    args = p.parse_args(argv)
    asyncio.run(run(dry_run=not args.commit, db_url=args.db_url,
                    allow_production=args.allow_production, skip_extract=args.skip_extract))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
