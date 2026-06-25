"""ORM model for Approval Events (audit log for document approval actions)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class ApprovalEvent(UUIDPrimaryKey, Base):
    __tablename__ = "approval_events"

    document_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # pr | po | pa
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    document_number: Mapped[str] = mapped_column(String(40), nullable=False)
    step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # submitted, approved, returned, rejected, cancelled, issued
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
