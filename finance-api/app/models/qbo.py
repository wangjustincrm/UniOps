"""QuickBooks Online mirror tables (finance owns the schema; migration 0027).

Read-only mirror of the QBO company — no association, no posting. Columns were
defined from real production payloads; anything unmapped stays in `raw`.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class QboSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per UI/CLI-triggered import; doubles as the live progress feed."""
    __tablename__ = "qbo_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)          # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    watermarks: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
