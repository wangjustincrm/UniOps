"""An order sent wrong, corrected and sent again (user 2026-11-13, as a transfer's amend): until the warehouse
has shipped it (no applied 945), a person changes a line's item or quantity, or takes a line off, with why.
The EA follow the item's units (never rounded, never a guessed factor). Then the 940 goes again: a live 940
is Replaced by a new one (FR-OB-03c); a rejected, failed or timed-out one is resent; a Pending order goes on
the next pass as usual. Retailer and manual orders only: a Shopify order's items come from the store."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.domain.order_rules import to_ea
from app.domain.status import Fulfillment as F
from app.models.config import Item, ItemUnit
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment
from app.platform.errors import BusinessError
from app.services import oplog

AMENDABLE = (F.PENDING, F.SENT_940, F.ACCEPTED_940, F.REJECTED_940, F.TIMEOUT_940, F.FAILED_940)
SOURCES = ("EDI_850", "MANUAL")


async def amend_order(uow, outbound, order_id: int, lines: list[dict[str, Any]], reason: str, *, carrier: str | None = None) -> dict:
    s = uow.session
    o = await s.get(Order, order_id, with_for_update=True)
    if o is None:
        raise BusinessError("order.not_found", "no such order", status_code=404)
    if o.historical:
        raise BusinessError("order.amend", "a historical order is not changed")
    if o.source_type not in SOURCES:
        raise BusinessError("order.amend_source", "a Shopify order's items come from the store: fix the item mapping, not the order")
    if F(o.fulfillment_status) not in AMENDABLE:
        raise BusinessError("order.amend", f"an order in {o.fulfillment_status} cannot be amended: only before the warehouse ships it")
    if (await s.execute(select(Shipment.id).where(Shipment.order_id == o.id, Shipment.applied.is_(True)))).first():
        raise BusinessError("order.amend", "the warehouse has shipped this order (945): it can no longer be amended")
    current = {ln.id: ln for ln in (await s.execute(select(OrderLine).where(OrderLine.order_id == o.id)
                                                    .order_by(OrderLine.line_no).with_for_update())).scalars()}
    codes = {str(x["item_code"]) for x in lines if x.get("item_code") and not x.get("removed")}
    items = {i.item_code for i in (await s.execute(select(Item).where(Item.item_code.in_(codes), Item.active.is_(True)))).scalars()}
    units: dict[str, dict[str, Decimal]] = {}
    for code, unit, factor in (await s.execute(select(ItemUnit.item_code, ItemUnit.unit, ItemUnit.factor_ea).where(
            ItemUnit.item_code.in_(codes), ItemUnit.active.is_(True)))).all():
        units.setdefault(code, {})[unit] = Decimal(factor)
    before, after, problems = [], [], []
    for x in lines:
        ln = current.get(int(x["id"]))
        if ln is None or ln.removed:
            raise BusinessError("order.amend_line", f"line {x['id']} is not on this order")
        before.append({"line": ln.line_no, "item": ln.item_code, "qty": str(ln.ordered_qty), "unit": ln.order_unit, "ea": ln.qty_ea})
        if x.get("removed"):
            ln.removed = True
            after.append({"line": ln.line_no, "removed": True})
            continue
        code, qty = str(x["item_code"]), Decimal(str(x["ordered_qty"]))
        if code not in items:
            problems.append(f"line {ln.line_no}: {code} is not an active item")
            continue
        ea, why = to_ea(qty, ln.order_unit, units.get(code, {}))
        if ea is None:
            problems.append(f"line {ln.line_no}: {why}")
            continue
        ln.item_code, ln.ordered_qty, ln.qty_ea = code, qty, ea
        ln.factor_ea = units.get(code, {}).get(ln.order_unit or "", Decimal(1) if ln.order_unit == "EA" else None)
        ln.problem = None
        after.append({"line": ln.line_no, "item": code, "qty": str(qty), "unit": ln.order_unit, "ea": ea})
    if problems:
        raise BusinessError("order.amend_line", "; ".join(problems))
    if all(ln.removed for ln in current.values()):
        raise BusinessError("order.amend_line", "an order needs at least one line: cancel it instead")
    if carrier is not None:
        if o.source_type != "MANUAL":
            raise BusinessError("order.amend", "only a manual order's carrier is chosen here")
        before.append({"carrier": o.shipping_method})
        o.shipping_method = carrier
        after.append({"carrier": carrier})
    uow.set_reason(f"order amended: {reason}")
    await s.flush()
    status = F(o.fulfillment_status)
    sent = None
    if status in (F.SENT_940, F.ACCEPTED_940):
        sent = await outbound.send_now(uow, o.id, replace=True)
    elif status in (F.REJECTED_940, F.TIMEOUT_940, F.FAILED_940):
        sent = await outbound.send_now(uow, o.id, replace=False)
    await oplog.write(s, "INFO", "order", f"order {o.label} amended" + (", a new 940 goes" if sent else "") + f": {reason}",
                      order_id=o.id, document_id=sent.id if sent else None, context={"before": before, "after": after})
    await s.flush()
    return {"document_id": sent.id if sent else None, "sent": sent is not None}
