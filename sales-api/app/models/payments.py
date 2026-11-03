"""Shopify payments and refunds (user 2026-11-03; migration sales17): what
was paid and refunded on an order, read-only from the store.

- `sales_order_payments`: one row per Shopify transaction (sale,
  authorization, capture, refund, void), the refund's transactions included
  (`refund_id`). No card data is ever stored — not here and not in `raw`
  (integrations/shopify.py strips it before anything sees the JSON).
- `sales_order_refunds` with their refunded lines and adjustments (a shipping
  refund, a refund discrepancy).

Rows are upserted by their Shopify id on every pass that sees the order
changed; nothing is deleted. FR-OB-11b (a refund before the 940 changes the
lines) is the order sync's, not these tables'.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.platform.entities import Audited


class OrderPayment(Audited, Base):
    __tablename__ = "sales_order_payments"
    __table_args__ = (UniqueConstraint("shopify_id", name="uq_sales_order_payments_shopify"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False, index=True)
    shopify_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    refund_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_refunds.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)        # sale | authorization | capture | refund | void
    gateway: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # success | pending | failure | error
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # the gateway's receipt / payment id
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)    # as received, card data stripped


class OrderRefund(Audited, Base):
    __tablename__ = "sales_order_refunds"
    __table_args__ = (UniqueConstraint("shopify_id", name="uq_sales_order_refunds_shopify"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False, index=True)
    shopify_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Shopify created_at
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    restock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lines_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # refunded lines, tax included
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    adjustments_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # other adjustments
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # its successful refund transactions
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class OrderRefundLine(Base):
    __tablename__ = "sales_order_refund_lines"
    __table_args__ = (UniqueConstraint("shopify_id", name="uq_sales_order_refund_lines_shopify"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("sales_order_refunds.id"), nullable=False, index=True)
    shopify_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_lines.id"), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    restock_type: Mapped[str | None] = mapped_column(String(20), nullable=True)


class OrderRefundAdjustment(Base):
    __tablename__ = "sales_order_refund_adjustments"
    __table_args__ = (UniqueConstraint("shopify_id", name="uq_sales_order_refund_adjustments_shopify"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("sales_order_refunds.id"), nullable=False, index=True)
    shopify_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str | None] = mapped_column(String(40), nullable=True)  # shipping_refund | refund_discrepancy
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)   # as Shopify gives it (negative = refunded)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
