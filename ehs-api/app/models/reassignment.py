"""One handover of a leaver's open work (round 10): who left, why, who did
it, and what went to whom. The field changes themselves are in the change log
under the person who carried it out."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Reassignment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_reassignments"

    leaver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    leaver_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    done_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {kind: {label, to_id, to_name, count, refs[]}} and how many of their own
    # tasks were closed.
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
