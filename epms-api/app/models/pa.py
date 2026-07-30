"""ORM models for Payment Application (PA) and its line items."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PaymentApplication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_applications"

    pa_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Physically nullable: OA (expense-api) shares this table for PO-less Direct
    # PAs (NULL po_id/po_number). EPMS owns PO-based PAs only — its list/detail
    # endpoints filter Direct PAs out (see crud.pa.get_all / api guards).
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # JSON arrays of UUID strings for linked invoices / GRs
    invoice_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    gr_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # regular | prepayment | settlement | balance
    pa_type: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")

    # For settlement/balance PAs: references the original prepayment PA
    prepayment_pa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_applications.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )

    # 思路 A：Settlement PA 抵扣的预付金额。payment_amount 已是净付余款
    # (= subtotal+tax+shipping+other − prepayment_applied)。仅 settlement/balance 用。
    prepayment_applied: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    # mdm-api tax_codes.code + rate snapshot (NULL = manual / legacy)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    other_charges: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    other_charges_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    # draft | submitted | in_review | approved | processed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once when the PA is paid (finance-api payment executor / zero-cash
    # settlement). Unlike updated_at (onupdate=now()) this is never bumped by
    # unrelated writes — dashboards key their "Paid/Processed This Month" on it.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once when the PA reaches approved (approval engine). Dashboards key
    # "Approved Today" on it rather than the onupdate-bumped updated_at.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Prepayment fields
    prepayment_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    expected_settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Settlement (prepayment only)
    settlement_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pending | settled | disputed
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    settled_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settlement_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    settlement_variance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )

    line_items: Mapped[list["PaLineItem"]] = relationship(
        "PaLineItem", back_populates="pa", cascade="all, delete-orphan",
        lazy="selectin", order_by="PaLineItem.sort_order"
    )


class PaLineItem(UUIDPrimaryKey, Base):
    __tablename__ = "pa_line_items"

    pa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_applications.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("po_line_items.id", ondelete="SET NULL"),
        nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    pa: Mapped["PaymentApplication"] = relationship("PaymentApplication", back_populates="line_items")
