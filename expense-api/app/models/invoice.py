"""OA expense invoice models — owned by expense-api for PA-DIR flow."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ExpenseInvoice(UUIDPrimaryKey, TimestampMixin, Base):
    """Vendor invoice uploaded for PA-DIR payment creation."""
    __tablename__ = "expense_invoices"

    # ── File metadata (binary stored client-side / in browser) ───────────────
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ── OCR-extracted header fields ───────────────────────────────────────────
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    # ── OCR metadata ──────────────────────────────────────────────────────────
    ocr_raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    # list of field names below confidence threshold requiring user confirmation
    low_confidence_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # ── Status lifecycle ──────────────────────────────────────────────────────
    # uploaded → reviewed → used
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded", index=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when a PA is created from this invoice
    pa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pa_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    lines: Mapped[list["ExpenseInvoiceLine"]] = relationship(
        "ExpenseInvoiceLine", back_populates="invoice",
        cascade="all, delete-orphan", order_by="ExpenseInvoiceLine.line_number"
    )


class ExpenseInvoiceLine(UUIDPrimaryKey, Base):
    """Line items extracted from an expense invoice."""
    __tablename__ = "expense_invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_invoices.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # User fills these in Step 3
    budget_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    budget_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    invoice: Mapped["ExpenseInvoice"] = relationship("ExpenseInvoice", back_populates="lines")
