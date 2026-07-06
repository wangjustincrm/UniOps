"""Repair pass: re-link invoices stuck on "PMS Unknown Vendor".

The PMS invoice→PO link lives only on the PO/PA line rows (``InvoiceID``). An
incremental sync that filtered those line tables by Modified left new invoices
unable to resolve their PO, so they landed on the placeholder "PMS Unknown
Vendor" with a null po_id. This pass pulls the linkage tables IN FULL from
SharePoint, rebuilds ``InvoiceID → PO number``, and for every unknown-vendor
invoice whose PO exists in EPMS, restores po_id + the PO's vendor.

Invoices with no PO line referencing them (genuinely PO-less legacy invoices)
are left as-is and reported separately.

Safe by default (dry-run). Idempotent.

Usage (inside the epms-api container):
    python -m scripts.relink_unknown_invoices                     # dry-run
    python -m scripts.relink_unknown_invoices --commit --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from scripts.import_pms.extract import extract, load_staging

_REF = re.compile(r"INV-(\d+)")
UNKNOWN_VENDOR_NAME = "PMS Unknown Vendor"
PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


def _build_inv_to_pono(skip_extract: bool = False) -> dict[int, str]:
    """Full-pull po_item + pa_item, then map SharePoint InvoiceID → PO number."""
    if not skip_extract:
        print("Staging FULL po_item + pa_item from SharePoint ...", flush=True)
        extract(only={"po_item", "pa_item"}, since=None, skip_attachments=True)

    poitem_to_pono: dict[str, str] = {}
    inv_pono: dict[int, str] = {}
    for it in load_staging("po_item.json"):
        pono = str(it.get("Title") or "").strip()
        poitem_to_pono.setdefault(str(it.get("ID")), pono)
        iid = it.get("InvoiceID")
        if iid and pono:
            inv_pono.setdefault(int(iid), pono)
    # PA-item-only invoices: InvoiceID → POITEMID → PO number
    for it in load_staging("pa_item.json"):
        iid = it.get("InvoiceID")
        if not iid or int(iid) in inv_pono:
            continue
        pono = poitem_to_pono.get(str(it.get("POITEMID")))
        if pono:
            inv_pono[int(iid)] = pono
    print(f"  linkage rebuilt: {len(inv_pono)} InvoiceID → PO mappings")
    return inv_pono


async def relink(dry_run: bool, db_url: str | None = None,
                 allow_production: bool = False, skip_extract: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    inv_pono = _build_inv_to_pono(skip_extract=skip_extract)

    stats: dict[str, int] = defaultdict(int)
    samples: dict[str, list] = {"fixed": [], "po_missing": [], "no_link": []}

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            po_by_num = {
                num: (pid, vid, vname)
                for num, pid, vid, vname in (await db.execute(select(
                    PurchaseOrder.number, PurchaseOrder.id,
                    PurchaseOrder.vendor_id, PurchaseOrder.vendor_name))).all()
            }
            unknown = (await db.execute(
                select(Invoice).where(Invoice.vendor_name == UNKNOWN_VENDOR_NAME)
            )).scalars().all()
            stats["unknown_total"] = len(unknown)

            for inv in unknown:
                m = _REF.match(inv.internal_ref or "")
                if not m:
                    stats["no_ref"] += 1
                    continue
                pono = inv_pono.get(int(m.group(1)))
                if not pono:
                    stats["no_link"] += 1
                    if len(samples["no_link"]) < 10:
                        samples["no_link"].append(inv.internal_ref)
                    continue
                po = po_by_num.get(pono)
                if not po:
                    stats["po_missing"] += 1
                    if len(samples["po_missing"]) < 10:
                        samples["po_missing"].append((inv.internal_ref, pono))
                    continue
                pid, vid, vname = po
                inv.po_id = pid
                inv.po_number = pono
                inv.vendor_id = vid
                inv.vendor_name = vname
                stats["fixed"] += 1
                if len(samples["fixed"]) < 10:
                    samples["fixed"].append((inv.internal_ref, pono, vname))

            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    _print_stats(stats, samples, dry_run)
    return dict(stats)


def _print_stats(stats, samples, dry_run) -> None:
    print(
        f"\nRe-link unknown-vendor invoices ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  unknown-vendor invoices:   {stats.get('unknown_total', 0)}\n"
        f"  ✓ re-linked (fixed):       {stats.get('fixed', 0)}\n"
        f"  · no PO line references it: {stats.get('no_link', 0)}  (genuinely PO-less — left as unknown)\n"
        f"  · PO not in EPMS:          {stats.get('po_missing', 0)}\n"
        f"  · unparseable ref:         {stats.get('no_ref', 0)}"
    )
    if samples["fixed"]:
        print("  fixed sample:", samples["fixed"][:5])
    if samples["po_missing"]:
        print("  po-missing sample:", samples["po_missing"][:5])
    if samples["no_link"]:
        print("  no-link sample:", samples["no_link"][:5])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.relink_unknown_invoices")
    p.add_argument("--commit", action="store_true", help="actually write (default dry-run)")
    p.add_argument("--db-url", help="explicit async DB URL (overrides settings)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    p.add_argument("--skip-extract", action="store_true",
                   help="reuse already-staged po_item/pa_item instead of re-pulling")
    args = p.parse_args(argv)
    asyncio.run(relink(dry_run=not args.commit, db_url=args.db_url,
                       allow_production=args.allow_production, skip_extract=args.skip_extract))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
