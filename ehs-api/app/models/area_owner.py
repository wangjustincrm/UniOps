"""Who covers which part of the plant."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class AreaOwner(UUIDPrimaryKey, Base):
    """A supervisor and an area of the plant they are responsible for.

    The area carries its children: named for "103 – Dryer" is named for the
    lines underneath it. The subtree test is a prefix comparison against
    `location_path`, which is why the path is stored and not only the id.
    """

    __tablename__ = "ehs_area_owners"
    __table_args__ = (UniqueConstraint("user_id", "location_id", name="uq_ehs_area_owner"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                   nullable=False, index=True)
    location_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
