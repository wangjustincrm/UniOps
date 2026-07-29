"""Read-write mirror of EPMS purchase_requests — workflow execution only."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseRequest(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_requests"

    number: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Over-budget pre-approval — set by epms-api on PR create/update;
    # read by approval-api engine.py at submit time to inject pre-approval steps.
    over_budget: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    over_budget_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
