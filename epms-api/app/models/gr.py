"""ORM models for Goods Receipt (GR) and its line items."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class GoodsReceipt(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "goods_receipts"

    number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    po_number: Mapped[str] = mapped_column(String(40), nullable=False)

    pr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    pr_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # physical | service
    gr_type: Mapped[str] = mapped_column(String(20), nullable=False)
    procurement_type: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    # pending_ack | collection_pending | collected | confirmed | discrepancy | cancelled
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_ack", index=True)

    storage_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NC ERP provenance (NULL for non-NC GRs)
    source: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(50), nullable=True)

    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collection_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )

    line_items: Mapped[list["GrLineItem"]] = relationship(
        "GrLineItem", back_populates="gr", cascade="all, delete-orphan",
        lazy="selectin", order_by="GrLineItem.sort_order"
    )


class GrLineItem(UUIDPrimaryKey, Base):
    __tablename__ = "gr_line_items"

    gr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("po_line_items.id", ondelete="SET NULL"),
        nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    material_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qty_ordered: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    qty_received: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # good | discrepancy | damaged
    condition: Mapped[str] = mapped_column(String(20), nullable=False, default="good")
    discrepancy_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual_qty: Mapped[Decimal | None] = mapped_column(Numeric(15, 4), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    gr: Mapped["GoodsReceipt"] = relationship("GoodsReceipt", back_populates="line_items")
