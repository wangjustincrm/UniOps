"""Once per database (user 2026-11-16, order 1018769): each 945 row's stock move gets the moment its 945 reached Sales,
kept within its ship day (stock.ship_moment), in place of noon of that day. A 945 that came after a Radiant report of
the same day then counts after it; the next stock sync corrects the report's own moves.

    python -m app.scripts.repair_shipment_move_times            # dry run: how many moves would change
    python -m app.scripts.repair_shipment_move_times --apply
"""
from __future__ import annotations

import collections

from sqlalchemy import select

from app.core.deps import uow_factory
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import StockMove
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.stock import ship_moment, sync


async def main(args: ScriptArgs) -> dict:
    n: collections.Counter = collections.Counter()
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        rows = (await s.execute(select(StockMove, Shipment.ship_date, Shipment.created_at)
                                .join(ShipmentRow, StockMove.source_key == func_str(ShipmentRow.id))
                                .join(Shipment, Shipment.id == ShipmentRow.shipment_id)
                                .where(StockMove.source_type == "shipment_row").with_for_update(of=StockMove))).all()
        for m, ship_date, arrived in rows:
            at = ship_moment(ship_date, arrived)
            if at == m.at:
                n["unchanged"] += 1
                continue
            n["moved"] += 1
            if args.apply:
                m.at = at
        if args.apply and n["moved"]:
            await s.flush()
            out = await sync(u)  # the Radiant reports' own moves follow
            n.update({f"sync {k}": v for k, v in out.items()})
            await oplog.write(s, "INFO", "repair", "945 stock moves: the moment their 945 reached Sales, within the ship day", context=dict(n))
            await u.commit()
    return {"outcome": dict(n)}


def func_str(col):
    from sqlalchemy import String, cast
    return cast(col, String)


if __name__ == "__main__":
    script_main(main)
