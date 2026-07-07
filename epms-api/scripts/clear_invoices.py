"""Delete ALL EPMS invoices so the PMS importer can re-insert them under the
current rules (discard invoices with no PO/PA line reference; prefer the
PO-bound duplicate over an earlier no-PO upload).

Removes, in FK-safe order (keeping PR / PO / PA / GR intact):
  invoice_po_allocations → invoice_tax_lines → invoice_attachments(source='epms')
  → clear payment_applications.invoice_ids (they'd otherwise dangle) → invoices.

After re-importing invoices, rebuild the PA→invoice links with
scripts.rebuild_pa_invoice_links (PA.invoice_ids is only built on PA INSERT, and
the re-import re-uses existing PAs, so it must be rebuilt separately).

⚠️ Deletes EVERY row in `invoices` (manually-uploaded ones too, not just PMS).
⚠️ Finance AP (ap_invoices) is a separate store; this does not touch it.

Safe by default (dry-run). Idempotent.

Usage (inside the epms-api container):
    python -m scripts.clear_invoices                          # dry-run (counts only)
    python -m scripts.clear_invoices --commit --allow-production
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

PRODUCTION_HOSTS = ("10.10.50.20",)


def _is_production(url: str) -> bool:
    return any(h in url for h in PRODUCTION_HOSTS)


async def run(dry_run: bool, db_url: str | None = None, allow_production: bool = False) -> dict:
    url = db_url or settings.DATABASE_URL
    host = url.split("@")[-1].split("/")[0]
    if _is_production(url) and not allow_production:
        raise SystemExit(
            f"REFUSING to run against production DB ({host}); re-run with "
            f"--allow-production if that is truly intended."
        )
    print(f"Target DB: {host}  ({'DRY-RUN' if dry_run else 'WILL COMMIT'})\n")

    engine = create_async_engine(url, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    stats: dict[str, int] = {}
    try:
        async with sf() as db:
            async def scalar(sql: str) -> int:
                return (await db.execute(text(sql))).scalar() or 0

            # ── counts (before) ──────────────────────────────────────────────
            stats["invoices"] = await scalar("select count(*) from invoices")
            stats["allocations"] = await scalar("select count(*) from invoice_po_allocations")
            stats["tax_lines"] = await scalar("select count(*) from invoice_tax_lines")
            stats["attachments_epms"] = await scalar(
                "select count(*) from invoice_attachments where invoice_source='epms'")
            stats["pa_with_links"] = await scalar(
                "select count(*) from payment_applications "
                "where invoice_ids is not null and invoice_ids::text not in ('[]','null')")
            _print_stats(stats)

            # ── delete (children → clear PA links → invoices) ────────────────
            await db.execute(text("delete from invoice_po_allocations"))
            await db.execute(text("delete from invoice_tax_lines"))
            await db.execute(text("delete from invoice_attachments where invoice_source='epms'"))
            await db.execute(text(
                "update payment_applications set invoice_ids='[]'::jsonb "
                "where invoice_ids is not null and invoice_ids::text not in ('[]','null')"))
            res = await db.execute(text("delete from invoices"))
            stats["invoices_deleted"] = res.rowcount or 0

            if dry_run:
                await db.rollback()
                print("\nDRY-RUN — rolled back, nothing written.")
            else:
                await db.commit()
                print(f"\nCOMMITTED — deleted {stats['invoices_deleted']} invoices "
                      f"(+ children) and cleared {stats['pa_with_links']} PA link lists.")
    finally:
        await engine.dispose()
    return stats


def _print_stats(stats: dict) -> None:
    print("Will delete / clear:")
    print(f"  invoices:                 {stats['invoices']}")
    print(f"  invoice_po_allocations:   {stats['allocations']}")
    print(f"  invoice_tax_lines:        {stats['tax_lines']}")
    print(f"  invoice_attachments(epms):{stats['attachments_epms']}")
    print(f"  PAs whose invoice_ids get cleared: {stats['pa_with_links']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.clear_invoices")
    p.add_argument("--commit", action="store_true", help="actually delete (default dry-run)")
    p.add_argument("--db-url", help="explicit async DB URL (overrides settings)")
    p.add_argument("--allow-production", action="store_true",
                   help="required to target the production DB (10.10.50.20)")
    args = p.parse_args(argv)
    asyncio.run(run(dry_run=not args.commit, db_url=args.db_url,
                    allow_production=args.allow_production))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
