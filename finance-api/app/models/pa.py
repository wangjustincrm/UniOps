"""Read-only mirror of EPMS PaymentApplication — Finance Core reads for AP payables view."""
import uuid
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin
from datetime import datetime


class PaymentApplication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_applications"

    pa_number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    pa_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # po_id IS NULL for OA Direct PAs (PA-DIR) — earlier nullable=False was wrong
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    expected_settlement_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Written by the payment executor when the PA is paid (see crud.payment_execute).
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
