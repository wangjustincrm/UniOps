"""Consignment (代储仓) weekly finished-goods stock count (Phase 1A Task 3).

DDL was already created by migration `mrp02_forecast_consignment` (Task 1) —
this model maps onto the pre-existing `mrp_consignment_stock` table; no new
migration is needed for it.

Only a lot number is entered by hand on the weekly count (single main
consignment warehouse, finished goods only per design doc 6.3/6.4).
`expiry_date` is derived by looking the lot up in WMS's `INV_LOT_ATT`
(app/services/wms_lot_lookup.py) and is nullable because that lookup can
legitimately come back empty (lot not found, or WMS unreachable) — this must
never block the save (design doc 6.3). `expiry_source` records where the
value came from: `'wms'` (auto-filled), `'manual'` (caller supplied it), or
`None` (no expiry recorded at all).
"""
import uuid
from datetime import date

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ConsignmentStock(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_consignment_stock"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_code", "material_code", "lot_no", "count_date",
            name="uq_mrp_consignment_stock_wh_mat_lot_date",
        ),
    )

    warehouse_code: Mapped[str] = mapped_column(String(50), default="MAIN", server_default="MAIN")
    material_code: Mapped[str] = mapped_column(String(50), index=True)
    lot_no: Mapped[str] = mapped_column(String(50))
    qty: Mapped[object] = mapped_column(Numeric(18, 3), default=0, server_default="0")
    count_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    expiry_source: Mapped[str | None] = mapped_column(String(20))  # 'wms'|'manual'|None
    entered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
