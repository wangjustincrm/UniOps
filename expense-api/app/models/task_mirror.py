"""Read-only mirror of the shared `tasks` table (written by approval-api).

approval-api creates a task per pending approval step. A task is either bound to a
specific user (assigned_user_id) or to a role (assigned_role, assigned_user_id NULL)
to be resolved against company_config.role_management at view time. expense-api reads
this to decide whether the current user may act on a claim — the authoritative answer,
rather than re-deriving approver resolution from role assignments.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskMirror(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assigned_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
