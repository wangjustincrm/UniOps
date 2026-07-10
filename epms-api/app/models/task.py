"""ORM model for Task inbox."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Task(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "tasks"

    # approve_pr | approve_po | revise_pr | revise_po | acknowledge_gr | collect_goods |
    # confirm_service_gr | settle_prepayment | link_invoice | create_pa
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")  # urgent | normal

    document_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # pr | po | pa | gr | invoice
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    document_number: Mapped[str] = mapped_column(String(40), nullable=False)

    # Role that should handle this task; specific user if directly assigned
    assigned_role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # 任务发起人(如 match 指派的 AP);历史任务为 NULL
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
