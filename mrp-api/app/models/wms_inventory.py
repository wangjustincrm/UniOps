"""Read-only mirror of Flux WMS inventory lots (Task 8).

Source: `INV_LOT join INV_LOT_ATT` on the live Flux WMS Oracle DB (design doc
appendix A). This table is a full-extract SNAPSHOT — app/services/wms_sync
replaces its entire contents on every sync (delete-all + insert in one
transaction), not an incremental upsert, so `sync_batch_id` identifies which
run produced the currently-visible rows (useful for auditing/debugging a
sync, not for reconciling partial state — there never is any).
"""
from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class WmsInventoryLot(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "wms_inventory_lots"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "material_code", "lot_no", name="uq_wms_inventory_lots_wh_mat_lot"),
    )

    warehouse_id: Mapped[str] = mapped_column(String(20), index=True)  # <- INV_LOT.WAREHOUSEID (always 'CANADA')
    material_code: Mapped[str] = mapped_column(String(50), index=True)  # <- INV_LOT.SKU
    lot_no: Mapped[str] = mapped_column(String(50), index=True)  # <- INV_LOT.LOTNUM

    # The unit the WAREHOUSE measures this in, from its packaging ladder
    # (BAS_PACKAGE_DETAILS.UOMDESCR at the base level). NOT the ERP's unit:
    # the ERP counts S0093 in PIECES because that is how it is sold, the
    # warehouse weighs it in KG because that is how it is stored, and these
    # quantities are the warehouse's.
    uom: Mapped[str | None] = mapped_column(String(20))

    qty: Mapped[object] = mapped_column(Numeric(18, 4), default=0)
    qty_allocated: Mapped[object] = mapped_column(Numeric(18, 4), default=0)
    qty_onhold: Mapped[object] = mapped_column(Numeric(18, 4), default=0)

    wms_status: Mapped[str | None] = mapped_column(String(10))  # <- INV_LOT_ATT.LOTATT08 (QLT_STS raw code)
    mapped_status: Mapped[str] = mapped_column(String(20), index=True)  # available|hold|expired (mrp_status_mapping + expiry override)

    production_date: Mapped[object | None] = mapped_column(Date)  # <- LOTATT01
    expiry_date: Mapped[object | None] = mapped_column(Date, index=True)  # <- LOTATT02
    inbound_date: Mapped[object | None] = mapped_column(Date)  # <- LOTATT03

    supplier_batch: Mapped[str | None] = mapped_column(String(100))  # <- LOTATT05
    supplier_code: Mapped[str | None] = mapped_column(String(50))  # <- LOTATT13
    source_doc: Mapped[str | None] = mapped_column(String(100))  # <- LOTATT14

    wms_edit_time: Mapped[object | None] = mapped_column(DateTime(timezone=False))  # <- INV_LOT.EDITTIME

    sync_batch_id: Mapped[str] = mapped_column(String(50), index=True)  # run timestamp string, see service.py
