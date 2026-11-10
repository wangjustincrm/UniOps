"""Delivery confirmation, invoices and returns (design §3.2; migration
sales11_invoicing; FR-OB-19, 30 … 35).

The order's `invoice_status` is the invoice state machine (PRD 9.1, with the
Blocked hold); these rows hold what was confirmed and what was invoiced.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.models.config import Partner, one_of
from app.models.orders import Order, order_fact
from app.platform.entities import Audited, CreationAudited, Versioned

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
Invoice.partner_name_sql = column_property(
    select(Partner.name).join(Order, Order.partner_code == Partner.code).where(Order.id == Invoice.order_id)
    .correlate_except(Order, Partner).scalar_subquery())


class InvoiceLine(Base):
    __tablename__ = "sales_invoice_lines"
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
