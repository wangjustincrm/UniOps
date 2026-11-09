"""Once (user 2026-11-09, order 9567): orders whose 945 was applied before
fc094c9a stayed in the status they had (mostly Archive suppressed), because
only Sent / Accepted / Timeout could become Shipped. This moves each such order
to Shipped, as the 945 would now, and closes its archive / review task.
Status and tasks only: no quantity (already set by the 945), no Shopify write,
no file. Idempotent.

    python -m app.scripts.ship_applied_945            # dry run: which orders, nothing kept
    python -m app.scripts.ship_applied_945 --apply    # move them, and log it to sales_log
"""
from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.status import FULFILLMENT, SHIPPED_WITHOUT_OUR_940
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.redact_card_documents import quiet_sql
from app.services import oplog
from app.services.problems import ORDER_ARCHIVED, ORDER_REVIEW, task_service

CLOSES = {F.ARCHIVE_SUPPRESSED: ORDER_ARCHIVED, F.REVIEW: ORDER_REVIEW}


async def main(apply: bool) -> dict:
    tasks = task_service(lambda: False)
    moved: list[dict] = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        rows = (await s.execute(
            select(Order, Shipment).join(Shipment, Shipment.order_id == Order.id)
            .where(Shipment.applied.is_(True),
                   Order.fulfillment_status.in_([x.value for x in SHIPPED_WITHOUT_OUR_940]))
            .order_by(Order.id).with_for_update(of=Order))).all()
        for order, sh in rows:
            was = F(order.fulfillment_status)
            if order.id in {m["order_id"] for m in moved}:
                continue
            moved.append({"order_id": order.id, "order": order.label, "from": was.value, "shipment": sh.id})
            if not apply:
                continue
            FULFILLMENT.transition(order, F.SHIPPED, actor=u.actor, events=u.events,
                                   reason=f"945 applied as shipment {sh.id} before fc094c9a (one-off repair)")
            if was in CLOSES:
                await tasks.resolve_problem(s, CLOSES[was], order.id)
        if apply and moved:
            await oplog.write(s, "INFO", "repair", f"ship_applied_945: {len(moved)} orders moved to Shipped",
                              context={"orders": moved})
            await u.commit()
    return {"orders": len(moved), "detail": moved}


if __name__ == "__main__":
    quiet_sql()
    print(json.dumps({"apply": "--apply" in sys.argv, **asyncio.run(main("--apply" in sys.argv))}))
