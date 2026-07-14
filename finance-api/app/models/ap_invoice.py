"""Accounts Payable — finance-owned AP invoice master (mirror of AR module).

EPMS + OA upload vendor invoices; both are AP invoices owned here. Sources
(epms / oa) keep their own working tables; finance owns the normalized header +
tax lines via upsert keyed on (source, source_invoice_id). The posting spine
stays the single source of truth (accrual on `posted`).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

DRAFT = "draft"
POSTED = "posted"
PARTIALLY_PAID = "partially_paid"
PAID = "paid"
VOID = "void"
OPEN_STATUSES = (POSTED, PARTIALLY_PAID)


class ApInvoice(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ap_invoices"
    __table_args__ = (
        UniqueConstraint("ap_invoice_number", name="uq_ap_invoices_number"),
        UniqueConstraint("source", "source_invoice_id", name="uq_ap_invoices_source"),
    )

    ap_invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source_invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT, index=True)
    source_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    nc_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nc_export_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ApInvoiceTaxLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ap_invoice_tax_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ap_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    taxable_base: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
