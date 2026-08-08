"""Monthly net-requirement rollup (Phase 1A Task 4).

**Opening stock definition — this is the number everything downstream (MPS,
then MRP) depends on, so it lives here and nowhere else:**

    opening_stock = wms_available_qty + consignment_latest_count_qty

- **WMS side** (`wms_inventory_lots`, Phase 0's mirror of Flux WMS, refreshed
  ~every 30 minutes): sum of `qty - qty_onhold` over rows where
  `mapped_status = 'available'` AND (`expiry_date IS NULL` OR
  `expiry_date >= today`). This is the design doc's (appendix A /
  app/api/v1/inventory.py) availability formula verbatim — note it is
  `qty - qty_onhold`, **not** `qty - qty_allocated - qty_onhold`: allocated
  stock is still physically on the shelf and countable as opening stock,
  only on-hold stock is excluded. A lot that is `mapped_status='hold'` or
  whose `expiry_date` has already passed contributes nothing, even though
  its `qty` is nonzero.

- **Consignment side** (`mrp_consignment_stock`, one hand-entered weekly
  count per warehouse/material/lot): a count is a **full snapshot** of that
  warehouse's consignment stock on the day it was taken, not an append-only
  per-lot ledger — summed over the lots whose `count_date` equals the
  **latest `count_date` for that (warehouse_code, material_code)**, never a
  lot's own individually-latest `count_date`. A lot counted on 2026-07-21,
  2026-07-28 and 2026-08-04 must contribute only the 2026-08-04 quantity
  (summing every historical count would triple-count a single physical
  lot) — AND a lot counted 500 on 2026-07-21 that is simply absent from the
  2026-07-28 snapshot (it shipped out, so the planner didn't re-enter it)
  must contribute **0** from 2026-07-28 onward, even though its own
  2026-07-21 row is still sitting in the table with qty=500. Grouping by
  each lot's own latest `count_date` instead of the warehouse+material's
  latest `count_date` (the bug this module used to have) never notices a
  lot has departed — it keeps counting that lot's last-known quantity
  forever, overstating opening stock and causing under-purchasing. This
  means the planner is expected to re-enter every lot still physically on
  hand at each week's count, including ones whose quantity hasn't changed —
  the snapshot is trusted to be a complete picture of what's there, not an
  incremental delta.

- **In-transit / already-planned-but-not-yet-received stock is 0 in this
  phase**, by design, not by omission: Phase 1A has not built the MPS/
  purchase-plan layer yet, so there is no "planned, not yet received"
  quantity to draw from. It becomes real only after Phase 1B releases a
  production/purchase plan (design doc §6.2 step ②'s third term) — this
  module does not fabricate a placeholder for it.

**Monthly rollforward** (`compute_net_requirements`): a pure function, no DB
access.

    opening_stock[month N]  = closing_stock[month N-1]   (month 1 uses the
                               caller-supplied `opening_stock` argument)
    net_requirement[month]  = max(0, forecast[month] - opening_stock[month])
    closing_stock[month]    = max(0, opening_stock[month] - forecast[month])

Months are always processed in chronological order — the input dict is
sorted by month key before rolling forward — regardless of the caller's
insertion order, because 'YYYY-MM' string ordering matches chronological
ordering and Python dicts do not guarantee iteration order matches intent.
Decimal is used throughout; float must never enter this calculation (see
feedback_uniops_decimal_as_string in project memory).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consignment import ConsignmentStock
from app.models.sync_state import MrpSyncState
from app.models.wms_inventory import WmsInventoryLot


@dataclass(frozen=True)
class NetRow:
    month: str
    forecast_qty: Decimal
    opening_stock: Decimal
    net_requirement: Decimal
    closing_stock: Decimal


def compute_net_requirements(
    forecast_by_month: dict[str, Decimal], opening_stock: Decimal,
) -> list[NetRow]:
    """Roll `opening_stock` forward month by month against `forecast_by_month`.

    See module docstring for the exact formulas. `forecast_by_month` keys
    are 'YYYY-MM' strings; they are sorted here so the caller's dict
    insertion order never matters.
    """
    zero = Decimal("0")
    rows: list[NetRow] = []
    running_opening = opening_stock
    for month in sorted(forecast_by_month):
        forecast_qty = forecast_by_month[month]
        net_requirement = max(zero, forecast_qty - running_opening)
        closing_stock = max(zero, running_opening - forecast_qty)
        rows.append(NetRow(
            month=month,
            forecast_qty=forecast_qty,
            opening_stock=running_opening,
            net_requirement=net_requirement,
            closing_stock=closing_stock,
        ))
        running_opening = closing_stock
    return rows


PLANNING_UOM = "KG"


class UomMismatchError(ValueError):
    """A contributing stock source is not in the planning UOM (KG) and no
    conversion exists — summing it would silently produce a wrong net
    requirement (design decision 2026-08-06: enforce, don't convert)."""


@dataclass(frozen=True)
class OpeningStockBreakdown:
    """Provenance/freshness breakdown behind a single material's opening
    stock number, for the UI's "where did this come from, how fresh is it"
    display (design doc §6.4)."""

    wms_qty: Decimal
    wms_uom: str
    consignment_qty: Decimal
    consignment_uom: str
    consignment_count_date: date | None
    wms_synced_at: datetime | None

    @property
    def opening_stock(self) -> Decimal:
        for label, qty, uom in (
            ("wms", self.wms_qty, self.wms_uom),
            ("consignment", self.consignment_qty, self.consignment_uom),
        ):
            if qty and uom != PLANNING_UOM:
                raise UomMismatchError(
                    f"{label} stock is in {uom!r}, not {PLANNING_UOM!r}; refusing to sum"
                )
        return self.wms_qty + self.consignment_qty


async def _wms_available_qty(db: AsyncSession, material_code: str, today: date) -> Decimal:
    stmt = select(
        func.coalesce(func.sum(WmsInventoryLot.qty - WmsInventoryLot.qty_onhold), 0)
    ).where(
        WmsInventoryLot.material_code == material_code,
        WmsInventoryLot.mapped_status == "available",
        sa.or_(WmsInventoryLot.expiry_date.is_(None), WmsInventoryLot.expiry_date >= today),
    )
    return Decimal((await db.execute(stmt)).scalar_one())


async def _consignment_latest_qty(
    db: AsyncSession, material_code: str,
) -> tuple[Decimal, str, date | None]:
    """Sum only the lots reported in the LATEST count_date **per
    (warehouse_code, material_code)** — a full-snapshot read, never a
    lot's own individually-latest count_date (see module docstring's I3
    note: that would let a lot that shipped out and stopped being counted
    keep contributing its last-known quantity forever).

    Also returns the `uom` off one of the summed rows (single warehouse/
    material in practice; if rows ever disagree, taking any is fine here —
    a mixed-unit guard is out of scope for this task) so the caller can
    enforce the KG planning-UOM invariant. Defaults to PLANNING_UOM when
    there are no rows to read a unit from."""
    latest_per_warehouse = (
        select(
            ConsignmentStock.warehouse_code,
            func.max(ConsignmentStock.count_date).label("latest_count_date"),
        )
        .where(ConsignmentStock.material_code == material_code)
        .group_by(ConsignmentStock.warehouse_code)
        .subquery()
    )
    qty_stmt = (
        select(func.coalesce(func.sum(ConsignmentStock.qty), 0))
        .select_from(ConsignmentStock)
        .join(
            latest_per_warehouse,
            sa.and_(
                ConsignmentStock.warehouse_code == latest_per_warehouse.c.warehouse_code,
                ConsignmentStock.count_date == latest_per_warehouse.c.latest_count_date,
            ),
        )
        .where(ConsignmentStock.material_code == material_code)
    )
    qty = Decimal((await db.execute(qty_stmt)).scalar_one())

    uom_stmt = (
        select(ConsignmentStock.uom)
        .select_from(ConsignmentStock)
        .join(
            latest_per_warehouse,
            sa.and_(
                ConsignmentStock.warehouse_code == latest_per_warehouse.c.warehouse_code,
                ConsignmentStock.count_date == latest_per_warehouse.c.latest_count_date,
            ),
        )
        .where(ConsignmentStock.material_code == material_code)
        .limit(1)
    )
    uom = (await db.execute(uom_stmt)).scalar_one_or_none() or PLANNING_UOM

    latest_date_stmt = select(func.max(ConsignmentStock.count_date)).where(
        ConsignmentStock.material_code == material_code
    )
    latest_date = (await db.execute(latest_date_stmt)).scalar_one_or_none()
    return qty, uom, latest_date


async def get_opening_stock_breakdown(
    db: AsyncSession, material_code: str, today: date | None = None,
) -> OpeningStockBreakdown:
    """The single entry point downstream code (the API layer, and later MPS)
    should call to get a material's opening stock + provenance. Never
    compute opening stock any other way — see module docstring."""
    resolved_today = today if today is not None else datetime.now(timezone.utc).date()

    wms_qty = await _wms_available_qty(db, material_code, resolved_today)
    consignment_qty, consignment_uom, consignment_count_date = await _consignment_latest_qty(
        db, material_code
    )

    sync_row = await db.get(MrpSyncState, "wms")
    wms_synced_at = sync_row.last_synced_at if sync_row is not None else None

    return OpeningStockBreakdown(
        wms_qty=wms_qty,
        # wms_inventory_lots has no unit column; finished-goods WMS stock is
        # KG by survey (2026-08-06 decision) — assert the invariant here
        # rather than read a column that doesn't exist.
        wms_uom=PLANNING_UOM,
        consignment_qty=consignment_qty,
        consignment_uom=consignment_uom,
        consignment_count_date=consignment_count_date,
        wms_synced_at=wms_synced_at,
    )
