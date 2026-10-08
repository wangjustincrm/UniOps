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


class NcPurchaseRefetchRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """A standing request for the incremental sync to re-read one NC order.

    The incremental filter is ``changed_at >= watermark``. Deleting a mirrored PO
    in Data Maintenance changes nothing in NC, so the order's change time stays
    where it was — behind a watermark the sync has long since passed — and the
    order is never read again. It does not come back on the next run, or any run:
    it is simply gone from UniOps while NC still lists it as live. PO-058-2607-02
    disappeared exactly that way.

    A row here is the missing signal. The reader unions these pks into the
    incremental fetch regardless of the watermark, so a delete heals itself on
    the next scheduled run instead of needing somebody to know that
    ``rewind_nc_purchase_watermark`` exists.

    Deliberately not a watermark rewind: winding the global watermark back to
    reach ONE order re-reads every order that changed since, which is a much
    larger blast radius for the same result.
    """
    __tablename__ = "nc_purchase_refetch_requests"

    #: PO_ORDER.pk_order — the natural key the whole mirror is resolved by.
    nc_source_pk: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    #: The document number at the time of the request, for the audit trail. The
    #: mirror row it named is gone, so this is the only thing left tying the
    #: request to something a human recognises.
    po_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Set once a run has settled the request. NULL = still pending, and the next
    #: incremental run picks it up.
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    #: How it was settled: 'mirrored' (the order came back) or 'gone_from_nc'
    #: (NC no longer lists it, so there is nothing to re-read and the request
    #: must not retry forever).
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
