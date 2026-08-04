"""Per-source sync watermark/status, mirroring mdm-api's erp_sync_state idiom.

Task 8 has exactly one row: source='wms'. Later MRP sync sources (NC BOM
mirror reuse, etc.) can add rows without a schema change.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MrpSyncState(Base):
    __tablename__ = "mrp_sync_state"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'wms' (Task 8); future sources add rows
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'success' | 'failed' | 'empty_extract'
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
