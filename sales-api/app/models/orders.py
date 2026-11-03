"""Orders and their lines (design §3.3; migration sales05_orders, P3).

An order comes from a Shopify store, a retailer 850 or a person (manual).
(partner_code, source_order_id) is unique: the same order arriving again
updates it (AC-OB-16). partner_code has no foreign key on purpose — an 850
naming an unknown partner is still created, and goes to Review (FR-OB-15).
Fulfillment and invoice statuses change only through their machines
(app/domain/status.py); the order state is derived (D17). Quantities sent to
the warehouse are EA (FR-OB-14): qty_ea = ordered_qty × factor_ea, never
rounded.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Sequence, String, Text,
    UniqueConstraint, false, func, select, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.domain.orders import HOLD_REASONS, LINE_STATUSES, SOURCE_TYPES
from app.domain.status import FULFILLMENT, INVOICE, Fulfillment, Invoice
from app.models.config import Partner, one_of
from app.platform.entities import Audited, Versioned

ORDER_NUMBER_SEQ = Sequence("sales_order_number_seq", start=1)  # P9 sets it above Zoho's highest


class Order(Audited, Versioned, Base):
    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("partner_code", "source_order_id", name="uq_sales_orders_source"),
        one_of("source_type", SOURCE_TYPES, "ck_sales_orders_source_type"),
        one_of("fulfillment_status", tuple(s.value for s in Fulfillment), "ck_sales_orders_fulfillment_status"),
        one_of("invoice_status", tuple(s.value for s in Invoice), "ck_sales_orders_invoice_status"),
        one_of("hold_reason", HOLD_REASONS, "ck_sales_orders_hold_reason"),
        Index("ix_sales_orders_fulfillment_status", "fulfillment_status"),
        # The Order ref (A.3 source reference), unique (sales15).
        Index("uq_sales_orders_source_reference", "source_reference", unique=True,
              postgresql_where=text("source_reference IS NOT NULL")),
    )

    order_number: Mapped[int] = mapped_column(BigInteger, ORDER_NUMBER_SEQ, nullable=False, unique=True,
                                              server_default=ORDER_NUMBER_SEQ.next_value())
    partner_code: Mapped[str] = mapped_column(String(50), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("sales_connections.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False)
    source_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_order_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The Order ref — the order's visible identifier (A.3 source reference, user 2026-11-03):
    # partner abbreviation, "-", the source order number (a manual order: its internal
    # number). Set once at creation and never changed (a later abbreviation change leaves it).
    source_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    department: Mapped[str | None] = mapped_column(String(20), nullable=True)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    do_not_deliver_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancel_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ship_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)   # snapshot
    bill_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_method: Mapped[str | None] = mapped_column(String(255), nullable=True)  # sales08: W66 GND / EXP
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="CAD")
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    shipping: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    discount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fulfillment_status: Mapped[str] = mapped_column(String(20), nullable=False)
    fulfillment_status_before_hold: Mapped[str | None] = mapped_column(String(20), nullable=True)
    invoice_status: Mapped[str] = mapped_column(String(40), nullable=False)
    invoice_status_before_hold: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hold_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # why it is in Review (FR-OB-15)
    historical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())  # FR-OB-17
    # sales09: received during the shadow run — Zoho handles it; never sent a 940 by itself (§5.4).
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    first_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # D20 delay start
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_in_shopify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    shopify_financial_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shopify_fulfillment_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shopify_fulfillment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # AC-OB-72
    warehouse_order_number_received: Mapped[str | None] = mapped_column(String(40), nullable=True)  # FR-OB-28
    zoho_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    @property
    def ref(self) -> str:
        """The Order ref; the internal number for an order created before it existed."""
        return self.source_reference or str(self.order_number)

    @property
    def partner_name(self) -> str | None:
        """The partner's name as loaded with the row (`partner_name_sql`); never a lazy load."""
        return self.__dict__.get("partner_name_sql")

    @property
    def total_amount(self) -> Decimal | None:
        """The order's amount as loaded with the row (`total_amount_sql`); never a lazy load."""
        return self.__dict__.get("total_amount_sql")


FULFILLMENT.bind(Order)
INVOICE.bind(Order)

# The partner's name on the order's row (lists sort, filter, group and search by it).
Order.partner_name_sql = column_property(
    select(Partner.name).where(Partner.code == Order.partner_code).correlate_except(Partner).scalar_subquery())


class OrderLine(Audited, Base):
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_sales_order_lines_line"),
        one_of("line_status", LINE_STATUSES, "ck_sales_order_lines_status"),
    )

    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(BigInteger, nullable=False)  # Shopify: the line id; 850: Line
    item_code: Mapped[str | None] = mapped_column(String(50), ForeignKey("sales_items.item_code"), nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # identifiers received (FR-OB-15)
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    order_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    factor_ea: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    qty_ea: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 5), nullable=True)
    line_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="Active")
    shipped_ea: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)  # why this line holds the order in Review


# The order's amount: the source's total (Shopify: current_total_price, after
# refunds), else its lines at the PO price (850 and manual orders carry no
# total) — what the Total column shows and the list sums.
Order.total_amount_sql = column_property(func.coalesce(Order.total, select(
    func.round(func.sum(OrderLine.ordered_qty * OrderLine.unit_price), 2)).where(
    OrderLine.order_id == Order.id, OrderLine.removed.is_(False)).correlate_except(OrderLine).scalar_subquery()))
