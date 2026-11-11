"""Reads every order the Shopify store has (read only) into Sales' history.

The orders below the store connection's cut-off (FR-OB-17) are Zoho's era: each is
taken as the Zoho history import takes Shopify pages (import_zoho_history.shopify_pages)
— every page stored as the file it is, each order made or updated from its latest
state, a fulfilled one Shipped, a finished one historical; problems are counted, never
raised as tasks; no 940, no Shopify write. Orders at or above the cut-off are left to
the store connection's own run (they are live). Nothing is written to Shopify.

    python -m app.scripts.read_shopify_history                 # dry run: reads the store, counts, writes nothing
    python -m app.scripts.read_shopify_history --apply         # needs automatic runs off and the shadow run off
"""
from __future__ import annotations

import collections
import os
from datetime import datetime, timezone
from pathlib import PurePath

from sqlalchemy import func, select

from app.core.deps import uow_factory
from app.integrations.shopify import HttpShopify
from app.models.config import Connection
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.scripts.import_zoho_history import History, refuse_unless_quiet
from app.services import oplog
from app.services.intake import ConnectionSnapshot

BATCH_PAGES = 20


def number(o: dict) -> int | None:
    raw = str(o.get("order_number") or str(o.get("name") or "").lstrip("#"))
    return int(raw) if raw.isdigit() else None


def older_than(page: dict, cutoff: int | None) -> dict:
    """The page with only the orders of Zoho's era (below the cut-off); all of them without one."""
    keep = [o for o in page.get("orders") or [] if cutoff is None or ((n := number(o)) is not None and n < cutoff)]
    return {**page, "orders": keep}


async def main(args: ScriptArgs) -> dict:
    if args.apply:
        await refuse_unless_quiet(uow_factory)
    async with uow_factory(SYSTEM_ACTOR) as u:
        conn = (await u.session.execute(select(Connection).where(Connection.kind == "SHOPIFY")
                                        .order_by(Connection.active.desc(), Connection.id).limit(1))).scalar_one()
        snap, cutoff, partner = ConnectionSnapshot.of(conn), conn.order_number_cutoff, conn.partner_code
        here = set((await u.session.execute(select(Order.source_order_id).where(Order.partner_code == partner))).scalars())
        before = await u.session.scalar(select(func.count()).select_from(Order).where(Order.partner_code == partner))
    shopify = HttpShopify(secret=os.environ.get)
    h = History(uow_factory, PurePath("."))
    n: collections.Counter = collections.Counter()
    seen = {"store_orders": 0, "older": 0, "older_new": 0, "live_left": 0}
    batch: list = []
    page_info, page_no = None, 0
    while True:
        page = await shopify.orders_page(snap, page_info=page_info, limit=250)
        page_no += 1
        orders = page.orders
        seen["store_orders"] += len(orders)
        kept = older_than({"orders": orders}, cutoff)
        seen["older"] += len(kept["orders"])
        seen["live_left"] += len(orders) - len(kept["orders"])
        seen["older_new"] += sum(1 for o in kept["orders"] if str(o.get("id")) not in here)
        if args.apply and kept["orders"]:
            at = datetime.now(timezone.utc)
            batch.append((PurePath(f"shopify-store-{at:%Y%m%d_%H%M%S}-page-{page_no:04d}.json"), kept, at))
            if len(batch) >= BATCH_PAGES:
                await h.apply_shopify_pages(iter(batch), n)
                batch = []
        if not page.next_page_info:
            break
        page_info = page.next_page_info
    if args.apply and batch:
        await h.apply_shopify_pages(iter(batch), n)
    async with uow_factory(SYSTEM_ACTOR) as u:
        after = await u.session.scalar(select(func.count()).select_from(Order).where(Order.partner_code == partner))
        if args.apply:
            await oplog.write(u.session, "INFO", "repair", f"Shopify history read from the store: {seen['older']} orders "
                              f"below the cut-off {cutoff}", context={"pages": page_no, **seen, **dict(n)})
            await u.commit()
    return {"cutoff": cutoff, "pages": page_no, **seen, "orders_before": before, "orders_after": after,
            "outcome": dict(n), "would_be_tasks": dict(h.problems.raised)}


if __name__ == "__main__":
    script_main(main)
