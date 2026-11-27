"""Lots (traceability and recall; user 2026-11-03): every 945 row with what a
recall needs beside it — its shipment (ship date, tracking, the 945), the
order it went out on (Order ref) and the customer (partner, ship-to city and
postal code). A read model over sales_shipment_rows: a SELECT, never a table,
so nothing is copied and nothing can drift; rows are never deleted (FR-OB-05).

Every 945 row is here, a shadow-run or second 945's too (marked): the goods on
it went out either way.
"""
from __future__ import annotations

from sqlalchemy import Integer, case, cast, exists, func, select

from app.db.base import Base
from app.models.config import Partner
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.platform.clock import business_date_sql


def _ship_to(key: str):
    """The 945's ship-to, else the order's."""
    return func.coalesce(func.nullif(Shipment.ship_to[key].astext, ""), Order.ship_to[key].astext)


def _trace(*extra):
    return (select(
        ShipmentRow.id.label("id"),
        ShipmentRow.lot.label("lot"),
        ShipmentRow.item_code.label("item_code"),
        ShipmentRow.expiry.label("expiry"),
        cast(ShipmentRow.expiry - business_date_sql(), Integer).label("days_to_expiry"),
        ShipmentRow.qty_shipped_ea.label("qty_shipped_ea"),
        ShipmentRow.sscc.label("sscc"),
        func.coalesce(ShipmentRow.tracking, Shipment.tracking[0].astext).label("tracking"),
        Shipment.id.label("shipment_id"),
        Shipment.document_id.label("document_id"),
        Shipment.ship_date.label("ship_date"),
        Shipment.bol.label("bol"),
        Shipment.carrier.label("carrier"),
        Shipment.our_reference.label("our_reference"),
        Shipment.applied.label("applied"),
        Shipment.shadow.label("shadow"),
        Order.id.label("order_id"),
        Order.source_reference.label("order_ref"),
        Order.order_number.label("order_number"),
        Order.partner_code.label("partner_code"),
        Partner.name.label("partner_name"),
        _ship_to("city").label("ship_to_city"),
        _ship_to("zip").label("ship_to_postal"),
        *extra,
    )
    .select_from(ShipmentRow)
    .join(Shipment, Shipment.id == ShipmentRow.shipment_id)
    .outerjoin(Order, Order.id == Shipment.order_id)
    .outerjoin(Partner, Partner.code == Order.partner_code))


_TRACE = _trace().subquery("sales_lot_trace")


class LotRow(Base):
    """One 945 row as a recall sees it (columns: _TRACE's labels)."""
    __table__ = _TRACE
    __mapper_args__ = {"primary_key": [_TRACE.c.id]}


# The customer as Shipped shows it (user 2026-11-27): a retailer by its partner; a Shopify customer by the initial of
# the ship-to name and the city only — enough to tell orders apart in a recall, not a person's name.
_INITIAL = func.upper(func.left(func.nullif(func.trim(func.coalesce(Shipment.ship_to["name"].astext, Order.ship_to["name"].astext)), ""), 1))
_CUSTOMER = case(
    (Order.source_type == "SHOPIFY", func.concat("Shopify · ", func.coalesce(_INITIAL + "., ", ""),
                                                  func.concat_ws(" ", _ship_to("city"), _ship_to("province")))),
    else_=Partner.name)
# Its delivery confirmed (Delivery confirmation): a line of the order has its delivered quantity.
_DELIVERED = exists().where(OrderLine.order_id == Order.id, OrderLine.delivered_qty.is_not(None))

_SHIPPED = (_trace(_CUSTOMER.label("customer"), _DELIVERED.label("delivery_confirmed"))
            .where(Shipment.applied.is_(True), Shipment.shadow.is_(False))
            .subquery("sales_shipped"))


class ShippedRow(Base):
    """One applied 945 row (Shipped, user 2026-11-27): what went out to a customer — not a shadow-run 945, not a
    second 945 kept only. Each with its customer and whether its delivery is confirmed (columns: _SHIPPED's labels)."""
    __table__ = _SHIPPED
    __mapper_args__ = {"primary_key": [_SHIPPED.c.id]}
