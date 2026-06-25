from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ErpSyncState(Base):
    __tablename__ = "erp_sync_state"

    kind: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'material' | 'supplier' | 'person'
    last_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'success' | 'failed' | 'running'
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
