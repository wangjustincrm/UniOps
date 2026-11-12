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
    UniqueConstraint, case, false, func, select, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column, undefer_group

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
    # TrueCommerce's mailbox of the buyer (850 EDIMailbox; Sobeys: one per division): its 810 goes back to it.
    edi_mailbox: Mapped[str | None] = mapped_column(String(40), nullable=True)
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
    # Shopify finance (sales17; read-only, app/services/shopify_finance.py): the sales
    # channel, tags, gateways, discount codes and tax lines; the original total, the
    # successful refunds and the net (original − refunded); the source update they are from.
    source_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_gateways: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    discount_codes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    tax_lines: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    original_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_refunded: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    finance_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shopify_fulfillment_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shopify_fulfillment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # AC-OB-72
    warehouse_order_number_received: Mapped[str | None] = mapped_column(String(40), nullable=True)  # FR-OB-28
    zoho_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    @property
    def ref(self) -> str:
        """The Order ref; the internal number for an order created before it existed."""
        return self.source_reference or str(self.order_number)

    @property
    def label(self) -> str:
        """Both identifiers, for task, notification and log text (user 2026-11-03):
        "LCL-4887314746 (internal no. 7)"; the internal number alone before refs."""
        n = f"internal no. {self.order_number}"
        return f"{self.source_reference} ({n})" if self.source_reference else n

    @property
    def partner_name(self) -> str | None:
        """The partner's name as loaded with the row (`partner_name_sql`); never a lazy load."""
        return self.__dict__.get("partner_name_sql")

    @property
    def total_amount(self) -> Decimal | None:
        """The order's amount as loaded with the row (`total_amount_sql`); never a lazy load."""
        return self.__dict__.get("total_amount_sql")

    @property
    def delivery_due_date(self) -> date | None:
        """When the delivery is due (`delivery_due_sql`); never a lazy load."""
        return self.__dict__.get("delivery_due_sql")

    def fact(self, name: str, default=None):
        """A value loaded with the row (`*_sql`); never a lazy load."""
        return self.__dict__.get(name, default)

    @property
    def backordered_ea(self) -> int | None:
        """What the 945 left unshipped, in EA (`backordered_ea_sql`); never a lazy load. Not shown any
        more (user 2026-11-12): only the Short shipped mark reads it."""
        v = self.__dict__.get("backordered_ea_sql")
        if v is None:
            v = self.__dict__.get("shadow_backordered_sql")  # the shadow run's 945: the same figure
        return None if v is None else int(v)


    @property
    def returned_ea(self) -> int | None:
        """What the customer returned of what shipped, over the lines (`returned_ea_sql`); None when nothing."""
        v = self.__dict__.get("returned_ea_sql")
        return None if v is None else int(v)


FULFILLMENT.bind(Order)
INVOICE.bind(Order)

def order_fact(expr):
    """A fact of the order computed in SQL (`*_sql`, 2026-11-10): loaded only by the reads that show
    an order (OrderRepository undefers the "facts" group). A job reading orders pays none of the
    subqueries; Order.fact() on a row read without them gives the default, so only the order's
    own reads (order_views) use them."""
    return column_property(expr, deferred=True, group="facts")


# The loader option that reads an order with its facts (OrderRepository, a test).
WITH_FACTS = undefer_group("facts")


# The partner's name on the order's row (lists sort, filter, group and search by it).
Order.partner_name_sql = order_fact(
    select(Partner.name).where(Partner.code == Order.partner_code).correlate_except(Partner).scalar_subquery())

# What the Status and its Detail read of the partner (app/domain/order_status.py).
def _partner_value(column):
    return order_fact(select(column).where(Partner.code == Order.partner_code).correlate_except(Partner).scalar_subquery())


Order.partner_invoice_method_sql = _partner_value(Partner.invoice_method)
Order.partner_sending_mode_sql = _partner_value(Partner.sending_mode)
Order.partner_settlement_mode_sql = _partner_value(Partner.settlement_mode)
Order.partner_delay_minutes_sql = _partner_value(Partner.delay_minutes)
# No carrier tells us a delivery date: it is due on the requested date (dc routing: do-not-deliver-after).
Order.delivery_due_sql = order_fact(case(
    (select(Partner.dc_routing_required).where(Partner.code == Order.partner_code).correlate_except(Partner).scalar_subquery(),
     Order.do_not_deliver_after), else_=Order.requested_delivery_date))


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
    # sales18: the price the invoice will carry, per order unit; NULL = the PO price.
    # Set before an invoice exists; the draft copies it, then the invoice is edited.
    invoice_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 5), nullable=True)
    line_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="Active")
    shipped_ea: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # sales25: what the customer returned (or refused) of what shipped, entered by a person before the
    # invoice goes (user 2026-11-12). Delivered = shipped − returned; the invoice bills the delivered.
    returned_ea: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)  # why this line holds the order in Review
    # Shopify (sales17): the line's taxes and discounts as the store gives them
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_lines: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)


# The order's amount: the source's total (Shopify: current_total_price, after
# refunds), else its lines at the PO price (850 and manual orders carry no
# total) — what the Total column shows and the list sums.
Order.total_amount_sql = order_fact(func.coalesce(Order.total, select(
    func.round(func.sum(OrderLine.ordered_qty * OrderLine.unit_price), 2)).where(
    OrderLine.order_id == Order.id, OrderLine.removed.is_(False)).correlate_except(OrderLine).scalar_subquery()))


# The order's backordered EA: the sum over its lines with an applied 945 (NULL
# before any) — the list's Not shipped EA column and view (labelled Backordered EA before 2026-11-12: a short shipment is never sent later).
# What the customer returned of what shipped, over the order's lines (the list's Returned EA).
Order.returned_ea_sql = order_fact(select(func.nullif(func.sum(OrderLine.returned_ea), 0)).where(
    OrderLine.order_id == Order.id, OrderLine.removed.is_(False)).correlate_except(OrderLine).scalar_subquery())

Order.backordered_ea_sql = order_fact(select(
    func.sum(func.greatest(OrderLine.qty_ea - OrderLine.shipped_ea, 0))).where(
    OrderLine.order_id == Order.id, OrderLine.removed.is_(False), OrderLine.shipped_ea.is_not(None),
    OrderLine.qty_ea.is_not(None)).correlate_except(OrderLine).scalar_subquery())
