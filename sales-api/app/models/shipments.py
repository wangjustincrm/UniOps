"""945 shipments and their rows (design §3.2; migration sales10_shipments).

A shipment is one 945 transaction set (document, set index). Its rows are
every W12 under every LX, keyed by position — never by tracking number, which
an LTL pallet does not have (FR-OB-25). `applied` is false for a second,
different 945 for an order already shipped: stored, never counted. `shadow`:
read during the shadow run — never applied to an order and never written to
Shopify, also after the switch-off.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, and_, exists, false, func, select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.models.config import Item, one_of
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.platform.entities import Audited, Versioned

SHOPIFY_STATUSES = ("Not needed", "Pending", "Done", "Failed", "Suppressed")


class Shipment(Audited, Versioned, Base):
    __tablename__ = "sales_shipments"
    __table_args__ = (
        UniqueConstraint("document_id", "set_index", name="uq_sales_shipments_document_set"),
        one_of("shopify_status", SHOPIFY_STATUSES, "ck_sales_shipments_shopify_status"),
    )

    document_id: Mapped[int] = mapped_column(ForeignKey("sales_documents.id"), nullable=False)
    set_index: Mapped[int] = mapped_column(Integer, nullable=False)
    set_control: Mapped[str | None] = mapped_column(String(9), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True, index=True)
    order_key_received: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ship_date_source: Mapped[str | None] = mapped_column(String(5), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tracking: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    bol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    our_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)  # header N9*E1: the 940's reference
    carrier_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ship_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    checks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)  # FR-OB-25d
    shopify_status: Mapped[str] = mapped_column(String(20), nullable=False)
    shopify_fulfillment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    shopify_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShipmentRow(Base):
    __tablename__ = "sales_shipment_rows"
    __table_args__ = (UniqueConstraint("shipment_id", "lx_index", "w12_index", name="uq_sales_shipment_rows_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("sales_shipments.id"), nullable=False)
    lx_index: Mapped[int] = mapped_column(Integer, nullable=False)
    w12_index: Mapped[int] = mapped_column(Integer, nullable=False)
    lx_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sscc: Mapped[str | None] = mapped_column(String(20), nullable=True)  # a string: leading zeros kept
    item_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty_ordered_ea: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_shipped_ea: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    tracking: Mapped[str | None] = mapped_column(String(80), nullable=True)
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_lines.id"), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


# How many of a document's 945 sets have no order (FR-OB-25c): Documents filters on it.
Document.shipments_without_order = column_property(
    select(func.count(Shipment.id)).where(Shipment.document_id == Document.id, Shipment.order_id.is_(None))
    .correlate_except(Shipment).scalar_subquery())


# What the Order status reads of the order's 945s and 940 (app/domain/order_status.py).
def _any_shipment(*where):
    return column_property(exists().where(Shipment.order_id == Order.id, *where).correlate_except(Shipment))


Order.has_945_sql = _any_shipment()
Order.shopify_failed_sql = _any_shipment(Shipment.shopify_status.in_(("Failed", "Suppressed")))
Order.shopify_pending_sql = _any_shipment(Shipment.shopify_status == "Pending")
Order.shopify_error_sql = column_property(
    select(Shipment.shopify_error).where(Shipment.order_id == Order.id, Shipment.shopify_error.is_not(None))
    .order_by(Shipment.id.desc()).limit(1).correlate_except(Shipment).scalar_subquery())
_order_940 = (DocumentLink.entity_type == "order", DocumentLink.entity_id == Order.id, Document.kind == "940_EDI",
              Document.direction == "OUT")
Order.file_note_sql = column_property(  # the latest 940's rejection (the 997's codes) or failure
    select(func.coalesce(Document.ack_detail, Document.error)).join(DocumentLink, DocumentLink.document_id == Document.id)
    .where(*_order_940).order_by(Document.id.desc()).limit(1).correlate_except(Document, DocumentLink).scalar_subquery())
Order.sent_940_at_sql = column_property(
    select(func.max(Document.sent_at)).join(DocumentLink, DocumentLink.document_id == Document.id).where(
        *_order_940).correlate_except(Document, DocumentLink).scalar_subquery())

# The shadow run's 945 left a warehouse line short (the Short shipped mark). Only a 945 whose
# rows are linked to the order counts: an unlinked one (a synthetic test file) says nothing.
_shadow_ea = (select(func.coalesce(func.sum(ShipmentRow.qty_shipped_ea), 0))
              .join(Shipment, Shipment.id == ShipmentRow.shipment_id)
              .where(ShipmentRow.order_line_id == OrderLine.id, Shipment.shadow.is_(True))
              .correlate(OrderLine).scalar_subquery())
Order.shadow_short_sql = column_property(and_(
    exists().where(ShipmentRow.shipment_id == Shipment.id, Shipment.order_id == Order.id, Shipment.shadow.is_(True),
                   ShipmentRow.order_line_id.is_not(None)).correlate(Order),
    exists().where(OrderLine.order_id == Order.id, OrderLine.removed.is_(False), OrderLine.item_code == Item.item_code,
                   Item.warehouse_held.is_(True), _shadow_ea < OrderLine.qty_ea).correlate(Order)))
