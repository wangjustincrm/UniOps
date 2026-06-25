"""BadgePrint ORM model — one row per badge print event.

Per VMS PRD §5.2. First print on a visit atomically transitions
status → checked_in; subsequent prints (reprints) require a reason and
do NOT re-check-in.
"""
import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class BadgePrint(UUIDPrimaryKey, Base):
    __tablename__ = "vms_badge_prints"

    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vms_visits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    printed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    printed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    reprint_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    template_used:  Mapped[str] = mapped_column(String(100), nullable=False, default="standard")
