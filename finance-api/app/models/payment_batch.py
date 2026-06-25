"""Payment batches (payment runs) — Phase a A4. finance-api owns these.

A batch groups approved PAs to pay together; executing it runs each line
through the unified payment executor (can_pay / SoD / period gate / posting /
invoice marking) and tags the resulting payment_records with batch_id.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

DRAFT = "draft"
EXECUTED = "executed"
CANCELLED = "cancelled"


class PaymentBatch(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_batches"

    batch_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    batch_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=DRAFT, index=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False, default="bank_transfer")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bank_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # funding bank (chosen at execute)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class PaymentBatchLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_batch_lines"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_batches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    doc_kind: Mapped[str] = mapped_column(String(20), nullable=False)   # pa | pa_dir
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|paid|failed
    payment_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
