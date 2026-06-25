"""Finance Core-owned PaymentRecord table — new table in the shared epms DB."""
import uuid
from datetime import date
from decimal import Decimal
from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class PaymentRecord(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_records"

    # Generic document reference (0003): pa | pa_dir | expense_claim | ...
    doc_kind: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Legacy PA-specific columns (nullable since 0003; kept for PA payments)
    pa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_applications.id"), nullable=True, index=True)
    pa_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)  # eft|cheque|wire|other
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    recorded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")  # pending|completed|cancelled
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # payment run (A4)
    bank_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # funding bank
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
