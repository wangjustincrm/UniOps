"""Consignment (代储仓) weekly stock count API (Phase 1A Task 3).

Single main consignment warehouse, finished goods only (design doc 6.3/6.4).
The planner enters product + qty + count date + lot number by hand; expiry
is never hand-typed — `POST .../stock` auto-fills it via `lookup_lot`
(app/services/wms_lot_lookup.py) when the caller doesn't supply one, and
marks `expiry_source='wms'`. If the caller does supply `expiry_date`
explicitly, that value wins and is marked `expiry_source='manual'` — no WMS
lookup is attempted in that case.

`lookup_lot` is imported as a bare name (not accessed via the
`wms_lot_lookup` module) so tests can `monkeypatch.setattr(consignment,
"lookup_lot", ...)` — same idiom app/services/wms_sync/service.py uses for
`fetch_inventory` (see that module's docstring).

WMS being unreachable or the lot not being found in WMS must NEVER block the
save (design doc 6.3, and `lookup_lot` itself never raises for either case —
see its docstring): the row is still written with `expiry_date=None`,
`expiry_source=None`, and the create response's `wms_lookup_found` field
tells the caller a lookup was attempted and came back empty, so the UI can
show "lot not found in WMS, expiry left blank" instead of silently showing
nothing.

A duplicate (warehouse_code, material_code, lot_no, count_date) — the
planner re-entering the same week's count for the same lot — is a 409, not
the raw 500 an unhandled IntegrityError would otherwise produce.

`GET /stock` (list) resolves each row's material `name` via
`app.services.mdm_client.resolve_material_names` — one batched call per
request, same degrade-on-failure contract as the forecast grid (see
app/api/v1/forecast.py's module docstring): mdm-api trouble means every
row's `name` comes back None, never a broken/5xx list. `POST /stock` (create)
and `GET /stock/{id}` don't carry `name` — the create form already knows the
product's name client-side (the planner picked it via MaterialPicker) and
there's no single-row detail view that needs it; only the list table
(mrp/src/pages/consignment/ConsignmentStockPage.tsx) renders a bare
`material_code` today.
"""
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.consignment import ConsignmentStock
from app.services.mdm_client import resolve_material_names
from app.services.wms_lot_lookup import list_lots, lookup_lot

router = APIRouter(prefix="/consignment", tags=["consignment"])

logger = logging.getLogger(__name__)

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.demand.write"))]


# ── Schemas ──────────────────────────────────────────────────────────────


class LotLookupResponse(BaseModel):
    found: bool
    production_date: date | None
    expiry_date: date | None


class LotHistoryItem(BaseModel):
    lot_no: str
    production_date: date | None
    expiry_date: date | None


class LotHistoryResponse(BaseModel):
    # Historical batch numbers for one product, sourced live from WMS
    # INV_LOT_ATT (newest expiry first). Empty — never an error — when WMS is
    # unreachable/unconfigured or the SKU has no lots, so the combo box just
    # offers no suggestions and hand entry still works (design doc 6.3).
    items: list[LotHistoryItem]


class ConsignmentStockCreate(BaseModel):
    warehouse_code: str = "MAIN"
    material_code: str
    lot_no: str
    qty: Decimal
    count_date: date
    expiry_date: date | None = None  # supplied -> manual; omitted -> auto lot-lookup


class ConsignmentStockUpdate(BaseModel):
    qty: Decimal | None = None
    count_date: date | None = None
    expiry_date: date | None = None  # any explicit patch of this marks expiry_source='manual'


class ConsignmentStockResponse(BaseModel):
    id: uuid.UUID
    warehouse_code: str
    material_code: str
    lot_no: str
    qty: Decimal
    count_date: date
    expiry_date: date | None
    expiry_source: str | None
    entered_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConsignmentStockCreateResponse(ConsignmentStockResponse):
    # None unless a WMS auto-lookup was actually attempted (i.e. the caller
    # did not supply expiry_date); True/False reports whether that lookup
    # found the lot — lets the UI distinguish "lot not found in WMS" from
    # "you gave us an explicit manual expiry".
    wms_lookup_found: bool | None = None


class ConsignmentStockListItem(ConsignmentStockResponse):
    # Populated only by GET /stock (list) via a single batched mdm-api call
    # for the whole page — see this module's docstring. None if the material
    # has no name in mdm-api, or if that lookup couldn't be completed (mdm-api
    # unavailable) — either way the list must still render, just without a
    # name for that row.
    name: str | None = None


class ConsignmentStockListResponse(BaseModel):
    items: list[ConsignmentStockListItem]
    total: int
    page: int
    page_size: int
    # Freshness signal for the UI ("Last counted: … (N days ago)"),
    # per-warehouse since a future second warehouse could count on a
    # different cadence — keyed even though the business currently runs
    # exactly one ('MAIN').
    latest_count_dates: dict[str, date]


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_stock_or_404(db: SessionDep, stock_id: uuid.UUID) -> ConsignmentStock:
    row = await db.get(ConsignmentStock, stock_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consignment stock row not found")
    return row


def _sub_to_uuid(payload: dict) -> uuid.UUID | None:
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/lot-lookup", response_model=LotLookupResponse)
async def lot_lookup(
    _: ReadDep,
    lot_no: str = Query(...),
    material_code: str = Query(...),
):
    # lookup_lot() is a blocking oracledb call (thick-mode sync driver) — run
    # it off the event loop so a slow/hung WMS host doesn't stall every other
    # request this process is handling (including /health). Same idiom
    # app/services/wms_sync/service.py and app/api/v1/forecast.py's import
    # endpoint already use for their own blocking calls (I6, final-phase
    # review).
    result = await anyio.to_thread.run_sync(lookup_lot, lot_no, material_code)
    return result


@router.get("/lot-history", response_model=LotHistoryResponse)
async def lot_history(
    _: ReadDep,
    material_code: str = Query(...),
):
    # list_lots() is a blocking oracledb call — off the event loop, same as
    # lot_lookup above, so a slow WMS host can't stall the worker.
    items = await anyio.to_thread.run_sync(list_lots, material_code)
    return {"items": items}


@router.post("/stock", response_model=ConsignmentStockCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_stock(body: ConsignmentStockCreate, db: SessionDep, payload: WriteDep):
    wms_lookup_found: bool | None = None
    expiry_date = body.expiry_date
    expiry_source: str | None = None

    if expiry_date is not None:
        expiry_source = "manual"
    else:
        # See lot_lookup()'s comment above — same "don't block the event
        # loop on a blocking Oracle call" reasoning applies here.
        result = await anyio.to_thread.run_sync(lookup_lot, body.lot_no, body.material_code)
        wms_lookup_found = result["found"]
        if result["found"]:
            expiry_date = result["expiry_date"]
            expiry_source = "wms"
        # not found -> expiry_date stays None, expiry_source stays None;
        # lookup_lot already logged a warning — never block the save here.

    row = ConsignmentStock(
        warehouse_code=body.warehouse_code,
        material_code=body.material_code,
        lot_no=body.lot_no,
        qty=body.qty,
        count_date=body.count_date,
        expiry_date=expiry_date,
        expiry_source=expiry_source,
        entered_by=_sub_to_uuid(payload),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"a stock count already exists for warehouse={body.warehouse_code} "
                f"material={body.material_code} lot={body.lot_no} count_date={body.count_date}"
            ),
        ) from exc
    await db.refresh(row)

    response = ConsignmentStockCreateResponse.model_validate(row)
    response.wms_lookup_found = wms_lookup_found
    return response


@router.get("/stock", response_model=ConsignmentStockListResponse)
async def list_stock(
    db: SessionDep,
    _: ReadDep,
    token: BearerToken,
    warehouse_code: str | None = Query(default=None),
    material_code: str | None = Query(default=None),
    lot_no: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    stmt = select(ConsignmentStock)
    count_stmt = select(func.count()).select_from(ConsignmentStock)
    if warehouse_code:
        stmt = stmt.where(ConsignmentStock.warehouse_code == warehouse_code)
        count_stmt = count_stmt.where(ConsignmentStock.warehouse_code == warehouse_code)
    if material_code:
        stmt = stmt.where(ConsignmentStock.material_code == material_code)
        count_stmt = count_stmt.where(ConsignmentStock.material_code == material_code)
    if lot_no:
        stmt = stmt.where(ConsignmentStock.lot_no == lot_no)
        count_stmt = count_stmt.where(ConsignmentStock.lot_no == lot_no)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(ConsignmentStock.count_date.desc(), ConsignmentStock.material_code, ConsignmentStock.lot_no)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    # One batched mdm-api round trip for this page (never per row) — skipped
    # entirely when the page is empty. Never raises: see
    # resolve_material_names' docstring — mdm-api trouble degrades every
    # row's name to None rather than breaking this list.
    names = await resolve_material_names(token) if rows else {}
    items: list[ConsignmentStockListItem] = []
    for row in rows:
        item = ConsignmentStockListItem.model_validate(row)
        item.name = names.get(row.material_code)
        items.append(item)

    latest_stmt = select(ConsignmentStock.warehouse_code, func.max(ConsignmentStock.count_date)).group_by(
        ConsignmentStock.warehouse_code
    )
    latest_rows = (await db.execute(latest_stmt)).all()
    latest_count_dates = {wh: latest for wh, latest in latest_rows}

    return {
        "items": items, "total": total, "page": page, "page_size": page_size,
        "latest_count_dates": latest_count_dates,
    }


@router.get("/stock/{stock_id}", response_model=ConsignmentStockResponse)
async def get_stock(stock_id: uuid.UUID, db: SessionDep, _: ReadDep):
    return await _get_stock_or_404(db, stock_id)


@router.patch("/stock/{stock_id}", response_model=ConsignmentStockResponse)
async def update_stock(stock_id: uuid.UUID, body: ConsignmentStockUpdate, db: SessionDep, _: WriteDep):
    row = await _get_stock_or_404(db, stock_id)

    if body.qty is not None:
        row.qty = body.qty
    if body.count_date is not None:
        row.count_date = body.count_date
    if body.expiry_date is not None:
        row.expiry_date = body.expiry_date
        row.expiry_source = "manual"

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="updating this row would collide with an existing (warehouse, material, lot, count_date)",
        ) from exc
    await db.refresh(row)
    return row


@router.delete("/stock/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stock(stock_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await _get_stock_or_404(db, stock_id)
    await db.delete(row)
    await db.commit()
