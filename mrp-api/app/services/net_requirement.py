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
  count per warehouse/material/lot): summed **only over each lot's LATEST
  `count_date`**. The table accumulates one row per week per lot, so a lot
  counted on 2026-07-21, 2026-07-28 and 2026-08-04 must contribute only the
  2026-08-04 quantity — summing every historical count would triple-count
  a single physical lot.

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


@dataclass(frozen=True)
class OpeningStockBreakdown:
    """Provenance/freshness breakdown behind a single material's opening
    stock number, for the UI's "where did this come from, how fresh is it"
    display (design doc §6.4)."""

    wms_qty: Decimal
    consignment_qty: Decimal
    consignment_count_date: date | None
    wms_synced_at: datetime | None

    @property
    def opening_stock(self) -> Decimal:
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
) -> tuple[Decimal, date | None]:
    """Sum each (warehouse_code, lot_no)'s qty at its own latest count_date
    only — never every historical count of the same lot (see module
    docstring)."""
    latest = (
        select(
            ConsignmentStock.warehouse_code,
            ConsignmentStock.lot_no,
            func.max(ConsignmentStock.count_date).label("latest_count_date"),
        )
        .where(ConsignmentStock.material_code == material_code)
        .group_by(ConsignmentStock.warehouse_code, ConsignmentStock.lot_no)
        .subquery()
    )
    qty_stmt = (
        select(func.coalesce(func.sum(ConsignmentStock.qty), 0))
        .select_from(ConsignmentStock)
        .join(
            latest,
            sa.and_(
                ConsignmentStock.warehouse_code == latest.c.warehouse_code,
                ConsignmentStock.lot_no == latest.c.lot_no,
                ConsignmentStock.count_date == latest.c.latest_count_date,
            ),
        )
        .where(ConsignmentStock.material_code == material_code)
    )
    qty = Decimal((await db.execute(qty_stmt)).scalar_one())

    latest_date_stmt = select(func.max(ConsignmentStock.count_date)).where(
        ConsignmentStock.material_code == material_code
    )
    latest_date = (await db.execute(latest_date_stmt)).scalar_one_or_none()
    return qty, latest_date


async def get_opening_stock_breakdown(
    db: AsyncSession, material_code: str, today: date | None = None,
) -> OpeningStockBreakdown:
    """The single entry point downstream code (the API layer, and later MPS)
    should call to get a material's opening stock + provenance. Never
    compute opening stock any other way — see module docstring."""
    resolved_today = today if today is not None else datetime.now(timezone.utc).date()

    wms_qty = await _wms_available_qty(db, material_code, resolved_today)
    consignment_qty, consignment_count_date = await _consignment_latest_qty(db, material_code)

    sync_row = await db.get(MrpSyncState, "wms")
    wms_synced_at = sync_row.last_synced_at if sync_row is not None else None

    return OpeningStockBreakdown(
        wms_qty=wms_qty,
        consignment_qty=consignment_qty,
        consignment_count_date=consignment_count_date,
        wms_synced_at=wms_synced_at,
    )
