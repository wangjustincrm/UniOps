"""COA sync run log — lightweight audit for the NC COA/aux sync.

One row per applied sync. Written AFTER the main transaction commits or rolls
back (see services/nc_coa_sync.apply): keeping it in the same transaction would
roll the failure record away exactly when it matters most.

No status/watermark/progress columns — the sync runs synchronously, so the row
is written once the work is over and there is no "in progress" state to report.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class CoaSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "coa_sync_runs"

    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accounts_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    accounts_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    accounts_deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    aux_items_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    aux_items_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
