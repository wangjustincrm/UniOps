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

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.config import one_of
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
