"""Accounts Receivable — finance-owned (Phase c framework).

There is no Sales module yet, so finance owns the AR invoice document. When a
Sales/CRM module arrives it becomes the system of record and finance mirrors it
(same pattern as AP's epms-owned invoices). The posting spine is the single
source of truth either way:

    revenue recognition (post):  debit  accounts_receivable  total
                                 credit revenue              pre-tax
                                 credit output_tax           per tax_code (GST/HST payable)
    customer receipt:            debit  bank                 amount
                                 credit accounts_receivable  amount

Amounts are positive; signs come from debit/credit. entity_id on every table.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

# statuses
DRAFT = "draft"
POSTED = "posted"
PARTIALLY_PAID = "partially_paid"
PAID = "paid"
VOID = "void"
OPEN_STATUSES = (POSTED, PARTIALLY_PAID)


class ArInvoice(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ar_invoices"
    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_ar_invoices_number"),
    )

    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)        # pre-tax
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ArInvoiceTaxLine(UUIDPrimaryKey, TimestampMixin, Base):
    """Output tax split by tax_code — feeds the GST/HST return output side."""
    __tablename__ = "ar_invoice_tax_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ar_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    taxable_base: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))


class ArReceipt(UUIDPrimaryKey, TimestampMixin, Base):
    """Cash received from a customer — its own document (idempotent posting key),
    optionally applied to a single invoice (on-account when invoice_id is null)."""
    __tablename__ = "ar_receipts"
    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_ar_receipts_number"),
    )

    receipt_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ar_invoices.id", ondelete="SET NULL"), nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="bank_transfer")
    bank_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
