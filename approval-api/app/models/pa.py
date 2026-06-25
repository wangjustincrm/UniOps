"""Read-write mirror of EPMS payment_applications — workflow execution only."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PaymentApplication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_applications"

    pa_number: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    invoice_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
