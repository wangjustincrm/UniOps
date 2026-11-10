"""Photographs on records that had nowhere to keep one.

Round 12, HAZ-03: "allow pictures on all forms" — this round hazards, JSAs and
permits (the default given to Kirk). One table for the three rather than a
table each: incidents and drills got their own when each was the only one,
and a fourth and fifth copy of the same eight columns is how they drift.

The bytes live in file-api; this row is the join, as for incident and drill
attachments. The owner is polymorphic, no FK, as sources are everywhere else.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class RecordAttachment(UUIDPrimaryKey, Base):
    __tablename__ = "ehs_record_attachments"
    __table_args__ = (Index("ix_ehs_record_attachments_owner", "owner_type", "owner_id"),)

    # hazard | jsa | permit
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    storage_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
