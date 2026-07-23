"""Remittance advice send log — finance-api owns this table.

Anchored on payment_records, not on batch lines: a `batch` scope covers every
record sharing a batch_id, a `payment` scope covers one record. Both produce
the same per-payee groups, so one log serves both.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

SCOPE_BATCH = "batch"
SCOPE_PAYMENT = "payment"
KIND_VENDOR = "vendor"
KIND_EMPLOYEE = "employee"
SENT = "sent"
FAILED = "failed"


class RemittanceNotification(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_remittance_notifications"

    scope_kind: Mapped[str] = mapped_column(String(10), nullable=False)      # batch | payment
    # No FK: points at payment_batches or payment_records depending on scope_kind.
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recipient_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # vendor | employee
    party_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    party_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_record_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)          # sent | failed
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
