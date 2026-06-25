"""ORM model for notification delivery log."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class NotificationLog(UUIDPrimaryKey, Base):
    """Records every notification delivery attempt."""

    __tablename__ = "notification_logs"

    # Reference to the task that triggered this notification (nullable — some are standalone)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Channel used: email | teams
    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    # Template key e.g. "pr_approval_request", "daily_pending_reminder"
    template_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    # ok | failed | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
