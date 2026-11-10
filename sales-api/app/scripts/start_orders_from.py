"""Once per test restart (user 2026-11-05): dev2 starts again from a date. Orders
before it go with everything hanging from them; received files stay, so nothing
is downloaded twice; a Shopify order that comes back later because someone
touched it arrives Historical (FR-OB-17), so it never joins the working list.

    python -m app.scripts.start_orders_from 2026-11-04            # dry run: counts, nothing kept
    python -m app.scripts.start_orders_from 2026-11-04 --apply    # delete, in one transaction

An order stays when its order date is on or after the start, or when its 850
reached us on or after it (a retailer dates the 850 the day before it arrives).
Transfers created before the start go too. sales_audit, sales_log, sales_runs and
sales_documents stay: they are the record of what happened. Refused outside the
shadow run, and refused when an order to delete has an invoice: there is no
legitimate way to lose either in production. Prints counts only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa

from app.models.config import Connection
from app.models.documents import DocumentLink
from app.models.invoices import DeliveryConfirmation, Invoice, OrderReturn
from app.models.orders import Order, OrderLine
from app.models.payments import OrderPayment, OrderRefund, OrderRefundAdjustment, OrderRefundLine
from app.models.shadow import ShadowMatch
from app.models.shipments import Shipment, ShipmentRow
from app.models.transfers import Transfer, TransferLine
from app.platform.actors import SYSTEM_ACTOR
from app.services import oplog
from app.services.settings import SETTINGS, SHADOW_MODE

STEP = "restart"


class Refused(Exception):
    pass


def kept(start: date):
    since = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    return sa.or_(Order.order_date >= start, sa.and_(Order.source_type == "EDI_850", Order.first_received_at >= since))


async def _ids(s, stmt) -> list[int]:
    return list((await s.execute(stmt)).scalars())


async def _delete(s, counts: dict[str, int], model, *where) -> None:
    counts[model.__tablename__] = (await s.execute(sa.delete(model).where(*where))).rowcount


def lowest_number(orders: list[str | None]) -> int | None:
    numbers = [int(n) for n in (str(o).lstrip("#") for o in orders if o) if n.isdigit()]
    return min(numbers) if numbers else None


async def run(uow_factory, start: date, *, apply: bool) -> dict[str, Any]:
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        if not await SETTINGS.get(s, SHADOW_MODE):
            raise Refused("only during the shadow run")
        await s.execute(sa.text("LOCK TABLE sales_orders IN SHARE ROW EXCLUSIVE MODE"))  # a run mid-way waits

        orders = await _ids(s, sa.select(Order.id).where(sa.not_(sa.func.coalesce(kept(start), False))))
        if await s.scalar(sa.select(sa.func.count()).select_from(Invoice).where(Invoice.order_id.in_(orders))):
            raise Refused("an order to delete has an invoice")
        shipments = await _ids(s, sa.select(Shipment.id).where(Shipment.order_id.in_(orders)))
        lines = await _ids(s, sa.select(OrderLine.id).where(OrderLine.order_id.in_(orders)))
        refunds = await _ids(s, sa.select(OrderRefund.id).where(OrderRefund.order_id.in_(orders)))
        transfers = await _ids(s, sa.select(Transfer.id).where(Transfer.created_at < datetime(
            start.year, start.month, start.day, tzinfo=timezone.utc)))

        counts: dict[str, int] = {}
        await _delete(s, counts, ShadowMatch, ShadowMatch.order_id.in_(orders))
        await _delete(s, counts, ShipmentRow, ShipmentRow.shipment_id.in_(shipments))
        await _delete(s, counts, Shipment, Shipment.id.in_(shipments))
        await _delete(s, counts, OrderPayment, OrderPayment.order_id.in_(orders))
        await _delete(s, counts, OrderRefundAdjustment, OrderRefundAdjustment.refund_id.in_(refunds))
        await _delete(s, counts, OrderRefundLine, OrderRefundLine.refund_id.in_(refunds))
        await _delete(s, counts, OrderRefund, OrderRefund.id.in_(refunds))
        await _delete(s, counts, DeliveryConfirmation, DeliveryConfirmation.order_id.in_(orders))
        await _delete(s, counts, OrderReturn, OrderReturn.order_id.in_(orders))
        await _delete(s, counts, OrderLine, OrderLine.id.in_(lines))
        await _delete(s, counts, DocumentLink, DocumentLink.entity_type == "order", DocumentLink.entity_id.in_(orders))
        counts["links_transfer"] = (await s.execute(sa.delete(DocumentLink).where(
            DocumentLink.entity_type == "transfer", DocumentLink.entity_id.in_(transfers)))).rowcount
        await _delete(s, counts, Order, Order.id.in_(orders))
        await _delete(s, counts, TransferLine, TransferLine.transfer_id.in_(transfers))
        await _delete(s, counts, Transfer, Transfer.id.in_(transfers))

        cutoffs = {}
        for conn in (await s.execute(sa.select(Connection).where(Connection.kind == "SHOPIFY"))).scalars():
            lowest = lowest_number(list((await s.execute(sa.select(Order.source_order_number).where(
                Order.connection_id == conn.id))).scalars()))
            if lowest is not None and lowest != conn.order_number_cutoff:
                cutoffs[conn.name] = {"from": conn.order_number_cutoff, "to": lowest}
                conn.order_number_cutoff = lowest

        left = await s.scalar(sa.select(sa.func.count()).select_from(Order))
        report = {"apply": apply, "start": start.isoformat(), "deleted": counts, "orders_left": left, "cutoff": cutoffs}
        if apply:
            await oplog.write(s, "INFO", STEP, f"Orders start again from {start.isoformat()}", context=report)
            await u.commit()
        else:
            await u.rollback()
    return report


if __name__ == "__main__":
    from app.core.deps import uow_factory
    from app.scripts.base import script_main
    script_main(lambda a: run(uow_factory, date.fromisoformat(a.positional[0]), apply=a.apply))
