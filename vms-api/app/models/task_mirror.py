"""Read+write mirror of epms-api's `tasks` table.

vms-api writes Task rows for training / PPE compliance gates so they show
up in the unified Portal task inbox (and in VMS's own task inbox view)
alongside approval tasks. Same pattern as how approval-api writes Task
rows for approval steps — both services share the table via the schema
owned by epms-api.

We don't model every column the production Task carries (due_date,
amount, vendor are EPMS-specific). They stay NULL on VMS-written rows.

The vms-api Alembic migrations DO NOT create or alter this table; epms-api
owns the schema. Registered with `Base.metadata` so tests can
materialize it via `create_all`.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Task(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "tasks"

    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")

    document_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    document_number: Mapped[str] = mapped_column(String(40), nullable=False)

    assigned_role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
