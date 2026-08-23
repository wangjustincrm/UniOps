"""Run registry for the NC purchase (order + arrival) sync."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class NcPurchaseSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_purchase_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)            # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_from: Mapped[str | None] = mapped_column(String(19), nullable=True)
    watermark_to: Mapped[str | None] = mapped_column(String(19), nullable=True)
    pos_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    po_lines_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    grs_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    gr_lines_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_no_vendor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Orders mirrored under a suffixed number because the ERP number was already
    #: held (another NC order carrying the same vbillcode, or a PMS import).
    renamed_number_collision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    #: Orders that could not be mirrored at all — no free document number. Should
    #: be 0; anything else means an order is missing from UniOps.
    skipped_number_collision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
