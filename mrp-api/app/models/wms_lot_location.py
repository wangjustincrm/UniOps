"""Read-only mirror of where each WMS lot physically sits.

Source: `INV_LOT_LOC_ID` on the live Flux WMS Oracle DB — the table the Phase 0
survey (design doc appendix A) recorded as "库位/FEFO 明细视图需要时用" and left
unconnected until now.

Like `wms_inventory_lots` this is a full-extract SNAPSHOT: `app/services/
wms_sync` replaces its entire contents on every run, in the same transaction,
so the two can never describe different moments in time.

## What the source survey established (2026-08-18, read-only against the live DB)

- **A lot really does span locations.** 3,404 lots sit in one, 25 in two or
  more, and one packaging lot is spread over 28. Storing location on
  `wms_inventory_lots` was therefore never an option; this needs its own grain.
- **`(WAREHOUSEID, SKU, LOTNUM, LOCATIONID, TRACEID)` is unique** — all 3,549
  source rows. `LOCATIONID` alone is not: `STAGECANADA` holds 15 pallets of one
  lot, 700 each, distinguished only by `TRACEID`. That is why the trace id is
  part of the key rather than a detail column.
- **Quantities reconcile exactly.** For all 3,429 active lots the location
  quantities sum to `INV_LOT.QTY`, to the decimal. Nothing is lost or
  double-counted by presenting stock this way.
- **`QCSTATUS` on this table is NULL on every row** and is not maintained.
  Quality status keeps coming from the lot's `LOTATT08`; reading it from here
  would have quietly blanked the column.
- `TRACEID` is `'*'` on 122 rows, meaning "none". Stored verbatim so the unique
  key stays faithful to the source; the API maps it to null for display.
- `LPN` is `'*'` or `'N'` and carries nothing useful, so it is not mirrored.

`zone_id` comes from `BAS_LOCATION` and is the only human-meaningful thing the
warehouse master offers about a location — there is no name or description
column. A bare `11040511` says less than `11040511 (YL)`.
"""
from sqlalchemy import Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class WmsLotLocation(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "wms_lot_locations"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "material_code", "lot_no", "location_id", "trace_id",
            name="uq_wms_lot_locations_natural_key",
        ),
        # The lookup this table exists to serve: "where is this lot", asked
        # once per expanded batch row.
        Index("ix_wms_lot_locations_lot", "material_code", "lot_no"),
    )

    warehouse_id: Mapped[str] = mapped_column(String(20), index=True)  # <- WAREHOUSEID
    material_code: Mapped[str] = mapped_column(String(50), index=True)  # <- SKU
    lot_no: Mapped[str] = mapped_column(String(50), index=True)  # <- LOTNUM
    location_id: Mapped[str] = mapped_column(String(50), index=True)  # <- LOCATIONID
    #: <- TRACEID, the handling unit at that location. Stored verbatim,
    #: including the source's '*' for "none", so the unique key above matches
    #: the source's own grain.
    trace_id: Mapped[str] = mapped_column(String(50))
    #: <- BAS_LOCATION.ZONEID. Null when the location is not in the master.
    zone_id: Mapped[str | None] = mapped_column(String(50))

    qty: Mapped[object] = mapped_column(Numeric(18, 4), default=0)
    qty_allocated: Mapped[object] = mapped_column(Numeric(18, 4), default=0)
    qty_onhold: Mapped[object] = mapped_column(Numeric(18, 4), default=0)

    sync_batch_id: Mapped[str] = mapped_column(String(50), index=True)
