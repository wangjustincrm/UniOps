"""Once (FR-OB-25c): make the orders of the shadow-run 945s that have none,
through the same service as the Create order action. A 945 whose reference names
no partner (a Zoho one-off send) goes to the Manual partner.

    python -m app.scripts.orders_from_945            # dry run: what it would do, nothing written
    python -m app.scripts.orders_from_945 --apply    # one transaction per shipment

Prints counts and order numbers only: no names, no addresses.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from app.core.deps import uow_factory
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.scripts.redact_card_documents import quiet_sql
from app.services.order_from_945 import OrderFrom945

UNNAMED_PARTNER = "Manual"


async def run(factory: Callable[..., Any], *, apply: bool) -> dict[str, Any]:
    async with factory(SYSTEM_ACTOR) as u:
        plans = await OrderFrom945(u).without_order()
    out: dict[str, Any] = {"apply": apply, "shipments": len(plans), "created": 0, "joined": 0, "refused": 0, "lines": 0,
                           "lines_not_in_master": 0, "orders": []}
    seen: set[str] = set()
    for p in plans:
        one = {"shipment": p.shipment_id, "warehouse_order": p.order_key, "po": p.po}
        if p.refused:
            out["refused"] += 1
            out["orders"].append(one | {"refused": p.refused})
            continue
        partner = p.partner_code or UNNAMED_PARTNER
        key = p.po or p.order_key or str(p.shipment_id)
        joins = p.order_id is not None or key in seen
        seen.add(key)
        if apply:
            try:
                async with factory(SYSTEM_ACTOR) as u:
                    order = await OrderFrom945(u).create(p.shipment_id, partner)
                    one |= {"order_ref": order.ref, "order_number": order.order_number}
                    await u.commit()
            except BusinessError as e:
                out["refused"] += 1
                out["orders"].append(one | {"refused": e.code})
                continue
        out["joined" if joins else "created"] += 1
        out["lines"] += len(p.lines)
        out["lines_not_in_master"] += sum(1 for ln in p.lines if ln.problem)
        out["orders"].append(one | {"joins": p.order_ref} if p.order_ref else one)
    return out


if __name__ == "__main__":
    quiet_sql()
    print(json.dumps(asyncio.run(run(uow_factory, apply="--apply" in sys.argv))))
