"""What the payment gateways say about the money (Finance, user 2026-11-17; migration sales32).

One shape for every gateway (Authorize.Net now, PayPal later), read-only from the gateway's own
reporting API:

- `sales_gateway_settlements`: one deposit to the bank — an Authorize.Net settled batch (later a
  PayPal withdrawal). Its sales / refunds as the gateway counts them; `fee_amount` where the
  gateway reports fees (Authorize.Net does not: it bills them monthly).
- `sales_gateway_transactions`: one transaction in the gateway, in its settlement, and — once
  matched — the Shopify payment row it is (`payment_row_id`, at most one each) and how it was
  matched (`match_method`).

Rows are upserted by (gateway, external id); nothing is deleted. `raw` holds what was kept of the
gateway's JSON: no card number, no holder, no address (integrations/authorize_net.py).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, false, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.gateways import GATEWAYS, MATCH_METHODS, TX_KINDS
from app.models.config import one_of
from app.platform.entities import Audited


class GatewaySettlement(Audited, Base):
    __tablename__ = "sales_gateway_settlements"
    __table_args__ = (
        UniqueConstraint("gateway", "external_id", name="uq_sales_gateway_settlements_external"),
        one_of("gateway", GATEWAYS, "ck_sales_gateway_settlements_gateway"),
        Index("ix_sales_gateway_settlements_settled_at", "settled_at"),
    )

    connection_id: Mapped[int] = mapped_column(ForeignKey("sales_connections.id"), nullable=False)
    gateway: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)      # the batch id
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)      # settledSuccessfully, …
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    sales_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    refund_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)   # where the gateway reports it
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    declined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voided_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Chargebacks and returned items in the batch statistics: not in Net, shown on their own.
    chargeback_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    chargeback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    returned_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    returned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The transaction list was read whole (all pages); until then the batch is read again.
    transactions_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class GatewayTransaction(Audited, Base):
    __tablename__ = "sales_gateway_transactions"
    __table_args__ = (
        UniqueConstraint("gateway", "external_id", name="uq_sales_gateway_transactions_external"),
        one_of("gateway", GATEWAYS, "ck_sales_gateway_transactions_gateway"),
        one_of("kind", TX_KINDS, "ck_sales_gateway_transactions_kind"),
        CheckConstraint("match_method IS NULL OR match_method IN (" + ", ".join(f"'{m}'" for m in MATCH_METHODS) + ")",
                        name="ck_sales_gateway_transactions_match_method"),
        # A Shopify payment is one gateway transaction.
        Index("uq_sales_gateway_transactions_payment", "payment_row_id", unique=True,
              postgresql_where=text("payment_row_id IS NOT NULL")),
        Index("ix_sales_gateway_transactions_settlement", "settlement_id"),
        Index("ix_sales_gateway_transactions_submitted_at", "submitted_at"),
    )

    connection_id: Mapped[int] = mapped_column(ForeignKey("sales_connections.id"), nullable=False)
    gateway: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)      # the gateway's transaction id
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("sales_gateway_settlements.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)             # sale | refund | void | other
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)     # as the gateway names it
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())  # money moved
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)   # positive; kind says the direction
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # a refund's original transaction
    details_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_row_id: Mapped[int | None] = mapped_column(ForeignKey("sales_order_payments.id"), nullable=True)
    match_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
