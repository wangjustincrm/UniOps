"""One-shot repair: attach the GRs that existing matched invoices should already
have been linked to.

Three gaps left matched invoices with gr_id NULL (empty 3-Way Match view, blocked
PA receipt gate, manual "Edit → add GR" for every single one):

1. invoices matched BY TOTAL AMOUNT allocate against the PO header (po_line_id
   NULL), so the GR-create hook's line-level lookup never found them;
2. invoices in status ``match_review`` were filtered out of that hook entirely;
3. goods received BEFORE the invoice was matched had no hook at all — match()
   only stored an explicit GR selection, and the match panel never sends one.

The code fixes are in ``crud/gr.py`` (1, 2) and ``crud/invoice.py`` (3); this
pass applies the same rules to the invoices that predate them. It reuses the very
same helpers, so the backfill and the live path cannot drift apart.

Only invoices that (a) carry allocation rows, (b) are in a matched-ish status,
and (c) have no GR link yet are touched — an invoice whose GR link was cleared on
purpose is indistinguishable from one that never had one, so review the dry-run
list before applying. Legacy PMS invoices (no allocation rows) are never touched.
Idempotent: re-running finds nothing left to do.

Usage (inside the epms-api container):
    python -m scripts.backfill_invoice_gr_links                        # dry-run
    python -m scripts.backfill_invoice_gr_links --apply --allow-production
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all ORM models
from app.core.config import settings
from app.crud.invoice import _apply_gr_selection, _discover_grs_for_allocations
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.schemas.invoice import AllocationInput

PRODUCTION_HOSTS = ("10.10.50.20",)
TARGET_STATUSES = ("matched", "match_review", "exception")


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def backfill(dry_run: bool, db_url: str | None = None,
                   allow_production: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    stats: dict[str, int] = defaultdict(int)
    linked: list[tuple[str, str, str]] = []

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            candidates = (await db.execute(
                select(Invoice).where(
                    Invoice.status.in_(TARGET_STATUSES),
                    Invoice.gr_id.is_(None),
                    Invoice.po_id.is_not(None),
                ).order_by(Invoice.internal_ref)
            )).scalars().all()
            stats["candidates"] = len(candidates)

            for inv in candidates:
                if inv.gr_ids:
                    stats["already_linked"] += 1
                    continue
                rows = (await db.execute(
                    select(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == inv.id)
                )).scalars().all()
                if not rows:
                    stats["no_allocations"] += 1   # fee-only / legacy PMS — leave alone
                    continue
                allocs = [AllocationInput(
                    invoice_line_id=r.invoice_line_id, po_id=r.po_id,
                    po_line_id=r.po_line_id, allocated_amount=r.allocated_amount,
                    allocated_tax=r.allocated_tax,
                ) for r in rows]
                gr_ids = await _discover_grs_for_allocations(db, allocs)
                if not gr_ids:
                    stats["no_gr_yet"] += 1
                    continue
                await _apply_gr_selection(db, inv, gr_ids)
                stats["linked"] += 1
                header_only = all(r.po_line_id is None for r in rows)
                linked.append((
                    inv.internal_ref,
                    f"{inv.status}{' /header-level' if header_only else ''}",
                    inv.gr_number or "",
                ))

            await db.flush()
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
    finally:
        await engine.dispose()

    print(
        f"Backfill invoice → GR links ({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  scanned (matched-ish, no GR):  {stats['candidates']}\n"
        f"  ✓ linked:                      {stats['linked']}\n"
        f"  · no GR exists yet:            {stats['no_gr_yet']}\n"
        f"  · no allocations (fee-only/PMS): {stats['no_allocations']}\n"
        f"  · already had gr_ids:          {stats['already_linked']}"
    )
    if linked:
        print("\n  invoice          status                 GR(s)")
        for ref, status, grs in linked:
            print(f"  {ref:<16} {status:<22} {grs}")
    return dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default is dry-run)")
    ap.add_argument("--allow-production", action="store_true")
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()
    asyncio.run(backfill(
        dry_run=not args.apply, db_url=args.db_url,
        allow_production=args.allow_production,
    ))


if __name__ == "__main__":
    main()
