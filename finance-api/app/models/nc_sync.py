"""NC65 sync run log — audit + incremental watermark + live progress.

One row per UI-triggered sync (full / incremental). The worker updates the
insert counters as it goes, so the row doubles as the progress feed.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class NcSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)     # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_from: Mapped[str | None] = mapped_column(String(19), nullable=True)
    watermark_to: Mapped[str | None] = mapped_column(String(19), nullable=True)
    vouchers_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    vouchers_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lines_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dims_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unmapped_cc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
