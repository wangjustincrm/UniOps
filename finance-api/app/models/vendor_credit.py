"""Vendor Credit — finance-owned credit-note ledger.

A vendor Credit Note reduces what we owe a vendor. It is NOT an invoice: it
never enters 3-way match and never becomes a PA. It accrues into a per-vendor
balance that Phase B nets off the next payment to that vendor.

Sign convention: every monetary column here is POSITIVE. A vendor document
printed as "-0.04" is stored as 0.04. "This is a credit" is expressed by the
table, not by a sign — mixed signs are a repeated source of defects in this
codebase (see the PO/GR/Invoice negative-unit-price series). abs() is applied
in app/crud/vendor_credit.py and nowhere else; CHECK (total_amount > 0) is the
backstop.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

PENDING_REVIEW = "pending_review"
AVAILABLE = "available"
EXHAUSTED = "exhausted"
VOID = "void"

SOURCE_UPLOAD = "upload"
SOURCE_QBO_IMPORT = "qbo_import"


class VendorCredit(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vendor_credits"

    credit_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)

    # No FK: business_partners is an mdm-owned mirror in this service, same as
    # PaymentRecord.vendor_id (app/models/payment.py:22).
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # The vendor's own credit-note number, e.g. "11DJ-MFHX-N4JG".
    vendor_credit_number: Mapped[str] = mapped_column(String(100), nullable=False)

    credit_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)          # pre-tax, positive
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)    # = amount + tax_amount

    applied_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PENDING_REVIEW, index=True)

    # Traceability only — a credit is never 3-way matched against this PO.
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance. Phase C populates the qbo_import variants.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_UPLOAD)
    source_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    opening_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    imported_from_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("total_amount > 0", name="ck_vendor_credits_total_positive"),
        CheckConstraint("applied_amount >= 0 AND remaining_amount >= 0",
                        name="ck_vendor_credits_nonneg"),
        CheckConstraint("applied_amount + remaining_amount = total_amount",
                        name="ck_vendor_credits_balance"),
        # A rejected/voided upload must not block re-uploading a corrected one.
        Index("uq_vendor_credits_vendor_docno",
              "vendor_id", "vendor_credit_number",
              unique=True,
              postgresql_where=text("source = 'upload' AND status <> 'void'")),
        Index("uq_vendor_credits_source_ref",
              "source", "source_ref",
              unique=True,
              postgresql_where=text("source_ref IS NOT NULL")),
        # Serves the Phase B FIFO lookup.
        Index("ix_vendor_credits_available",
              "vendor_id", "currency", "credit_date",
              postgresql_where=text("status = 'available' AND remaining_amount > 0")),
    )
