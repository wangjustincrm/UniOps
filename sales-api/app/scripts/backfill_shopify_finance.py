"""Every Shopify order's payments, refunds and refund adjustments (user 2026-11-16: complete on dev2 first, then
Finance's requirements). The same refresh as each pass (ShopifyIntake._refresh_finance), in batches until no order's
finance is missing or stale: an order that changed in Shopify since it was read is brought up to date first. Reads
only from Shopify (GET; a 429 waits and tries again); no card data is stored (as every pass).

    python -m app.scripts.backfill_shopify_finance            # dry run: how many orders are missing or stale
    python -m app.scripts.backfill_shopify_finance --apply    # fills them; prints progress per batch
"""
from __future__ import annotations

import time

from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import uow_factory
from app.models.config import Connection, Partner
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.shopify_finance import stale_finance
from app.services.shopify_intake import BarcodeLookup, ShopifyReport

BATCH = 100


async def stale_count(conn_id: int) -> int:
    async with uow_factory(SYSTEM_ACTOR) as u:
        return await u.session.scalar(select(func.count()).select_from(stale_finance(conn_id).subquery()))


async def main(args: ScriptArgs) -> dict:
    from app.runtime import build_runtime
    out: dict = {}
    async with uow_factory(SYSTEM_ACTOR) as u:
        conns = list((await u.session.execute(select(Connection).where(Connection.kind == "SHOPIFY", Connection.can_read.is_(True))
                                              .order_by(Connection.id))).scalars())
        abbrevs = {c.id: (await u.session.scalar(select(Partner.abbrev).where(Partner.code == c.partner_code))) for c in conns}
        snaps = [ConnectionSnapshot.of(c) for c in conns]
    intake = build_runtime(settings, uow_factory).shopify_intake
    for snap in snaps:
        before = await stale_count(snap.id)
        row = {"missing_or_stale_before": before}
        if args.apply and before:
            barcodes, refreshed, failed, t0 = BarcodeLookup(intake.shopify, snap), 0, 0, time.monotonic()
            while True:
                report = ShopifyReport()
                await intake._refresh_finance(snap, report, None, abbrevs[snap.id], barcodes, limit=BATCH)
                refreshed, failed = refreshed + report.finance_refreshed, failed + report.finance_failed
                left = await stale_count(snap.id)
                print(f"{snap.name}: {refreshed} filled, {failed} failed, {left} left ({time.monotonic() - t0:.0f}s)", flush=True)
                if left == 0 or report.finance_refreshed == 0:
                    break  # done, or only failures left (each logged; a later pass tries them again)
            row |= {"filled": refreshed, "failed": failed, "left": left}
            async with uow_factory(SYSTEM_ACTOR) as u:
                await oplog.write(u.session, "INFO", "repair", f"{snap.name}: Shopify payments and refunds filled for {refreshed} orders",
                                  connection_id=snap.id, context=row)
                await u.commit()
        out[snap.name] = row
    return out


if __name__ == "__main__":
    script_main(main)
