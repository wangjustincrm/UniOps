"""Delivery confirmation, invoices and returns (design §3.2; migration
sales11_invoicing; FR-OB-19, 30 … 35).

The order's `invoice_status` is the invoice state machine (PRD 9.1, with the
Blocked hold); these rows hold what was confirmed and what was invoiced.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, and_, case, cast, exists, func, not_, null, select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.domain.gateways import LABELS as GATEWAY_LABELS
from app.domain.gateways import PAYPAL_IN_BALANCE, SHOPIFY_GATEWAYS
from app.domain.order_payment import DUE, NOT_NEEDED, NOT_SETTLED, OVERDUE, SETTLED, TERMS_SQL_PATTERN, WAITING
from app.domain.status import Fulfillment as F
from app.models.config import Partner, one_of
from app.models.gateways import GatewaySettlement, GatewayTransaction
from app.models.payments import OrderPayment
from app.models.orders import Order, order_fact
from app.platform.clock import business_date_sql
from app.platform.entities import Audited, CreationAudited, Versioned
from app.platform.parts import PartOf

RETURN_KINDS = ("Cancelled after shipment", "Returned")


class DeliveryConfirmation(Audited, Versioned, Base):
    """FR-OB-31: the confirmed date becomes the invoice date; the delivered
    quantities are on the order lines (`delivered_qty`)."""
    __tablename__ = "sales_delivery_confirmations"
    __table_args__ = (UniqueConstraint("order_id", name="uq_sales_delivery_confirmations_order"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    confirmed_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Invoice(Audited, Versioned, Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (UniqueConstraint("invoice_number", name="uq_sales_invoices_number"),
                      UniqueConstraint("order_id", name="uq_sales_invoices_order"))

    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)  # IN-<order number>
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)          # the partner's, when built
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The retailer's proof-of-delivery / receiving number (sales15): record and PDF (A.9 has no 810 field).
    delivery_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def order_ref(self) -> str | None:  # loaded with the row (order_ref_sql); never a lazy load
        return self.__dict__.get("order_ref_sql")

    @property
    def partner_name(self) -> str | None:
        return self.__dict__.get("partner_name_sql")

    @property
    def order_number(self) -> int | None:  # the order's Internal no. (order_number_sql)
        return self.__dict__.get("order_number_sql")


# The order's ref and partner name on the invoice's row (the list searches and sorts by them).
Invoice.order_ref_sql = column_property(
    select(Order.source_reference).where(Order.id == Invoice.order_id).correlate_except(Order).scalar_subquery())
Invoice.order_number_sql = column_property(
    select(Order.order_number).where(Order.id == Invoice.order_id).correlate_except(Order).scalar_subquery())
# Its order's invoice status (the list's Status; filtered by the Ready to send view).
Invoice.status_sql = column_property(
    select(Order.invoice_status).where(Order.id == Invoice.order_id).correlate_except(Order).scalar_subquery())
Invoice.partner_name_sql = column_property(
    select(Partner.name).join(Order, Order.partner_code == Partner.code).where(Order.id == Invoice.order_id)
    .correlate_except(Order, Partner).scalar_subquery())


class InvoiceLine(Base):
    __tablename__ = "sales_invoice_lines"
    __part_of__ = PartOf(Invoice, "invoice_id")
    __audited_fields__ = frozenset({"qty", "unit_price", "amount"})  # what a person changes: an update is in the audit (2026-11-24)
    __table_args__ = (UniqueConstraint("invoice_id", "line_no", name="uq_sales_invoice_lines_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoices.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_lines.id"), nullable=True)
    item_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    buyer_part: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    uom: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 5), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class OrderReturn(CreationAudited, Base):
    """FR-OB-19: cancelled or returned after shipment — `lines`
    [{"order_line_id", "qty_ea"}]; it stops or flags invoicing."""
    __tablename__ = "sales_order_returns"
    __table_args__ = (one_of("kind", RETURN_KINDS, "ck_sales_order_returns_kind"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


# The Detail of an order whose invoice failed (app/domain/order_status.py).
Order.invoice_error_sql = order_fact(
    select(Invoice.error).where(Invoice.order_id == Order.id).correlate_except(Invoice).scalar_subquery())


# ── an order's Payment (app/domain/order_payment.py, the same rule; tests hold them together) ──────────────────────
# A Shopify order's captured payments and, through the matches Payments made (GatewayTransaction.payment_row_id),
# whether each is in a closed gateway settlement: the derived Settled (user 2026-11-23).
_CAPTURED = (OrderPayment.order_id == Order.id, OrderPayment.kind.in_(("sale", "capture")), OrderPayment.status == "success",
             OrderPayment.test.is_(False))
_MATCH = (GatewayTransaction.payment_row_id == OrderPayment.id, GatewayTransaction.kind == "sale")
_CLOSED = (*_MATCH, GatewayTransaction.settled.is_(True), GatewaySettlement.id == GatewayTransaction.settlement_id,
           GatewaySettlement.state.is_distinct_from(PAYPAL_IN_BALANCE))


def _count(*where):
    return (select(func.count(OrderPayment.id)).where(*_CAPTURED, *where)
            .correlate_except(OrderPayment, GatewayTransaction, GatewaySettlement).scalar_subquery())


CAPTURED_N = _count()
OTHER_N = _count(func.lower(func.coalesce(OrderPayment.gateway, "")).not_in(SHOPIFY_GATEWAYS))
MATCHED_N = _count(exists(select(GatewayTransaction.id).where(*_MATCH)))
CLOSED_N = _count(exists(select(GatewayTransaction.id).select_from(GatewayTransaction).join(
    GatewaySettlement, GatewaySettlement.id == GatewayTransaction.settlement_id).where(*_CLOSED)))
AUTO = and_(CAPTURED_N > 0, OTHER_N == 0, CLOSED_N == CAPTURED_N)
# The latest closed settlement holding one of its payments: the derived Settled's date and reference.
_LATEST = (select(GatewaySettlement.id).select_from(OrderPayment).join(GatewayTransaction, and_(*_MATCH))
           .join(GatewaySettlement, GatewaySettlement.id == GatewayTransaction.settlement_id)
           .where(*_CAPTURED, *_CLOSED).order_by(GatewaySettlement.settled_at.desc(), GatewaySettlement.id.desc()).limit(1)
           .correlate_except(OrderPayment, GatewayTransaction, GatewaySettlement).scalar_subquery())
AUTO_SETTLEMENT_ID = case((and_(Order.settled_on.is_(None), Order.fulfillment_status != F.DRAFT.value,
                                Order.source_type == "SHOPIFY", AUTO), _LATEST), else_=null())


def _payment_status_sql():
    partner_terms = select(Partner.payment_terms).where(Partner.code == Order.partner_code).correlate_except(Partner).scalar_subquery()
    days = cast(func.substring(func.lower(func.coalesce(Invoice.payment_terms, partner_terms)), TERMS_SQL_PATTERN), Integer)
    due = (select(Invoice.invoice_date + days).where(Invoice.order_id == Order.id, Invoice.sent_at.is_not(None))
           .order_by(Invoice.id.desc()).limit(1).correlate_except(Invoice).scalar_subquery())
    sent = exists(select(Invoice.id).where(Invoice.order_id == Order.id, Invoice.sent_at.is_not(None)).correlate_except(Invoice))
    today = business_date_sql()  # SQL's now(): a FixedClock does not freeze it
    shopify = Order.source_type == "SHOPIFY"
    return case(
        (Order.settled_on.is_not(None), SETTLED),                      # the manual mark always wins
        (Order.fulfillment_status == F.DRAFT.value, null()),
        (and_(shopify, AUTO), SETTLED),                                # derived: the gateway settled it
        (and_(Order.fulfillment_status == F.CANCELLED.value, not_(sent)), NOT_NEEDED),
        (and_(shopify, func.coalesce(Order.net_amount, Order.total) == 0), NOT_NEEDED),  # nothing left to receive
        (and_(shopify, CAPTURED_N > 0, OTHER_N == 0, MATCHED_N == CAPTURED_N, CLOSED_N == 0), WAITING),
        (Order.historical.is_(True), NOT_NEEDED),  # the old orders: shown as the gateway proves (above), never chased
        (due < today, OVERDUE),
        (due.is_not(None), DUE),
        else_=NOT_SETTLED)


Order.payment_status_sql = order_fact(_payment_status_sql())
# The derived Settled's settlement: its id (Settlements opens it), "PayPal settlement <id>" and its date (Toronto).
Order.auto_settlement_id_sql = order_fact(AUTO_SETTLEMENT_ID)
Order.auto_settlement_label_sql = order_fact(
    select(case(*[(GatewaySettlement.gateway == g, f"{label} settlement ") for g, label in GATEWAY_LABELS.items()],
                else_=GatewaySettlement.gateway + " settlement ") + GatewaySettlement.external_id)
    .where(GatewaySettlement.id == AUTO_SETTLEMENT_ID).correlate_except(GatewaySettlement).scalar_subquery())
Order.auto_settled_on_sql = order_fact(
    select(business_date_sql(GatewaySettlement.settled_at))
    .where(GatewaySettlement.id == AUTO_SETTLEMENT_ID).correlate_except(GatewaySettlement).scalar_subquery())
