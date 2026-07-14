"""NC AP export batches — audit trail for parallel-run exports to NC."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NcExportBatch(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_export_batches"

    exported_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filename: Mapped[str | None] = mapped_column(String(120), nullable=True)
