"""Lots (traceability and recall; user 2026-11-03): every 945 row with what a
recall needs beside it — its shipment (ship date, tracking, the 945), the
order it went out on (Order ref) and the customer (partner, ship-to city and
postal code). A read model over sales_shipment_rows: a SELECT, never a table,
so nothing is copied and nothing can drift; rows are never deleted (FR-OB-05).

Every 945 row is here, a shadow-run or second 945's too (marked): the goods on
it went out either way.
"""
from __future__ import annotations

from sqlalchemy import Integer, cast, func, select

from app.db.base import Base
from app.models.config import Partner
from app.models.orders import Order
from app.models.shipments import Shipment, ShipmentRow


def _ship_to(key: str):
    """The 945's ship-to, else the order's."""
    return func.coalesce(func.nullif(Shipment.ship_to[key].astext, ""), Order.ship_to[key].astext)


_TRACE = (
    select(
        ShipmentRow.id.label("id"),
        ShipmentRow.lot.label("lot"),
        ShipmentRow.item_code.label("item_code"),
        ShipmentRow.expiry.label("expiry"),
        cast(ShipmentRow.expiry - func.current_date(), Integer).label("days_to_expiry"),
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
    )
    .select_from(ShipmentRow)
    .join(Shipment, Shipment.id == ShipmentRow.shipment_id)
    .outerjoin(Order, Order.id == Shipment.order_id)
    .outerjoin(Partner, Partner.code == Order.partner_code)
    .subquery("sales_lot_trace")
)


class LotRow(Base):
    """One 945 row as a recall sees it (columns: _TRACE's labels)."""
    __table__ = _TRACE
    __mapper_args__ = {"primary_key": [_TRACE.c.id]}
