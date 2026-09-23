"""One row per field changed, on every form in the module."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class ChangeLogEntry(UUIDPrimaryKey, Base):
    """What a field was, what it became, who did it and when.

    Append-only at the database, not by convention: a trail somebody can
    quietly edit is not evidence.
    """

    __tablename__ = "ehs_change_log"

    table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    # Text, not uuid: the config table keys on an integer, and a change to a
    # configuration row is exactly what an audit asks about.
    row_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The document number where the row has one, so the trail reads on its own.
    row_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # insert | update | delete. A creation is one row, not one per column: the
    # record itself is the account of what it was created as.
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    field: Mapped[str | None] = mapped_column(String(63), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    changed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
