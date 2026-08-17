"""Inventory — what is in the warehouse, what is about to go out of date, and
what is on the way.

Three reads over the WMS lot mirror (`wms_inventory_lots`, a full-extract
snapshot replaced on every sync) joined to mdm's material master and, for the
on-order half, EPMS's purchase orders:

- `GET /inventory/lots` — browse and search individual lots.
- `GET /inventory/aging` — shelf life bucketed at 180 / 60 / 30 days, plus what
  has already expired.
- `GET /inventory/materials` — one row per material: on hand, available, on
  hold, on order, earliest arrival, next expiry.

All three are gated `mrp.report.view`.

Two rules hold across all of them.

**Raw milk is never counted.** Material class `0101 Raw Milk` (business rule,
2026-08-17) is excluded from stock and from on-order figures alike, from the one
definition in `app/services/in_transit.py`. Today the WMS mirror holds no
raw-milk lots — it arrives by tanker straight into production — so the stock
half of the rule changes nothing yet, which is exactly why it is encoded rather
than left to the fact that it currently does not matter.

**Filtering and paging happen in the database, in that order.** Filtering a page
after it has been cut gives pages of uneven length and a total that counts rows
it never shows.

Availability keeps the definition Phase 1 already uses:
`available = qty - qty_onhold` over lots whose `mapped_status` is `available`
(WMS status 02/Release and not expired).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, case, func, or_, select

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.epms_mirror import MdmMaterial
from app.models.wms_inventory import WmsInventoryLot
from app.services.in_transit import (
    RAW_MILK_CLASS_CODE,
    in_transit_by_material,
    open_lines_for_material,
)
from app.services.inventory_aging import (
    AGING_BUCKETS,
    BUCKET_LABELS,
    bucket_bounds,
    bucket_for,
    days_until,
    summarise,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]

#: Columns `GET /inventory/lots` may be sorted by. A whitelist, not a
#: pass-through: `sort` reaches SQL, and an unvalidated one is an injection.
_SORTABLE = {
    "material_code": WmsInventoryLot.material_code,
    "lot_no": WmsInventoryLot.lot_no,
    "expiry_date": WmsInventoryLot.expiry_date,
    "qty": WmsInventoryLot.qty,
    "inbound_date": WmsInventoryLot.inbound_date,
}


# ── Schemas ──────────────────────────────────────────────────────────────


class WmsInventoryLotResponse(BaseModel):
    id: uuid.UUID
    warehouse_id: str
    material_code: str
    # Joined from mdm's master. None means the master has no row for this code,
    # never that the lookup failed -- the join is in the same database as the
    # lots, so there is no lookup to fail.
    material_name: str | None = None
    lot_no: str
    qty: Decimal
    qty_allocated: Decimal
    qty_onhold: Decimal
    wms_status: str | None
    mapped_status: str
    production_date: date | None
    expiry_date: date | None
    inbound_date: date | None
    #: Negative once past. None when the lot has no expiry date at all.
    days_to_expiry: int | None = None
    #: Shelf-life band, or None for a lot that does not expire.
    aging_bucket: str | None = None
    supplier_batch: str | None
    supplier_code: str | None
    source_doc: str | None
    wms_edit_time: datetime | None
    sync_batch_id: str

    model_config = {"from_attributes": True}


class WmsInventoryLotListResponse(BaseModel):
    items: list[WmsInventoryLotResponse]
    total: int
    page: int
    page_size: int
    #: The date every bucket and countdown in this response was computed
    #: against, so a screen cannot silently disagree with the server about
    #: what "today" is.
    as_of: date


class AgingBucket(BaseModel):
    key: str
    label: str
    lots: int
    qty: Decimal


class AgingSummaryResponse(BaseModel):
    as_of: date
    buckets: list[AgingBucket]
    #: Lots with no expiry date: excluded from every bucket, reported here.
    #: Mostly packaging, which does not expire.
    no_expiry_lots: int
    no_expiry_qty: Decimal
    #: Echoes the filter, so a screen can say what it is showing.
    erp_class_code: str | None


class MaterialStockResponse(BaseModel):
    material_code: str
    material_name: str | None
    base_uom: str | None
    erp_class_code: str | None
    erp_class_name: str | None
    on_hand: Decimal
    available: Decimal
    allocated: Decimal
    on_hold: Decimal
    expired_qty: Decimal
    next_expiry: date | None
    days_to_next_expiry: int | None
    lots: int
    in_transit: Decimal
    earliest_arrival: date | None
    open_po_lines: int


class MaterialStockListResponse(BaseModel):
    items: list[MaterialStockResponse]
    total: int
    page: int
    page_size: int
    as_of: date


class OpenPoLineResponse(BaseModel):
    po_number: str
    vendor_name: str
    material_code: str
    ordered: Decimal
    received: Decimal
    remaining: Decimal
    unit: str
    expected_arrival: date | None
    #: True when the date came from the PO header (typed by a person) rather
    #: than the ERP's own line-level date.
    arrival_is_from_header: bool


# ── Shared query pieces ──────────────────────────────────────────────────


def _lots_joined_to_materials() -> Select:
    """WMS lots LEFT-joined to mdm's material master.

    Left, not inner: a lot whose material the master has not caught up with is
    still physically in the warehouse, and an inner join would make it vanish
    from a stock report — the one place a missing row must not mean a missing
    pallet.
    """
    return select(WmsInventoryLot, MdmMaterial).outerjoin(
        MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code)


def _not_raw_milk():
    """Raw milk is never counted as stock (business rule, 2026-08-17).

    `coalesce`, not `!= '0101'`: in SQL, `NULL != '0101'` is NULL rather than
    true, so a plain comparison would also drop every lot whose material has no
    master row — real pallets, silently gone. An unknown class is not raw milk.
    """
    return func.coalesce(MdmMaterial.erp_class_code, "") != RAW_MILK_CLASS_CODE


def _apply_lot_filters(
    stmt: Select,
    *,
    material_code: str | None,
    mapped_status: str | None,
    warehouse_id: str | None,
    erp_class_code: str | None,
    search: str | None,
    expiring_before: date | None,
    expiring_after: date | None,
    aging_bucket: str | None,
    today: date,
    with_expiry_only: bool,
) -> Select:
    """Every filter, applied in the database before any page is cut."""
    stmt = stmt.where(_not_raw_milk())
    if material_code:
        stmt = stmt.where(WmsInventoryLot.material_code == material_code)
    if mapped_status:
        stmt = stmt.where(WmsInventoryLot.mapped_status == mapped_status)
    if warehouse_id:
        stmt = stmt.where(WmsInventoryLot.warehouse_id == warehouse_id)
    if erp_class_code:
        stmt = stmt.where(MdmMaterial.erp_class_code == erp_class_code)
    if search and search.strip():
        # Material NAME is searchable because the master is joined in the same
        # database -- people look a lot up by whichever of code, name, lot or
        # supplier batch they happen to be holding.
        needle = f"%{search.strip()}%"
        stmt = stmt.where(or_(
            WmsInventoryLot.material_code.ilike(needle),
            WmsInventoryLot.lot_no.ilike(needle),
            WmsInventoryLot.supplier_batch.ilike(needle),
            MdmMaterial.name.ilike(needle),
        ))
    if expiring_before:
        stmt = stmt.where(WmsInventoryLot.expiry_date < expiring_before)
    if expiring_after:
        stmt = stmt.where(WmsInventoryLot.expiry_date > expiring_after)
    if aging_bucket:
        # Resolved from the SAME thresholds `bucket_for` uses, so the count on
        # a band and the rows behind it can never disagree. A caller that
        # rebuilt these windows from the day counts would be off by one at
        # three of the five boundaries -- lots that show in the total and in no
        # table anywhere.
        lower, upper = bucket_bounds(aging_bucket, today)
        stmt = stmt.where(WmsInventoryLot.expiry_date.is_not(None))
        if lower is not None:
            stmt = stmt.where(WmsInventoryLot.expiry_date >= lower)
        if upper is not None:
            stmt = stmt.where(WmsInventoryLot.expiry_date < upper)
    if with_expiry_only:
        stmt = stmt.where(WmsInventoryLot.expiry_date.is_not(None))
    return stmt


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/lots", response_model=WmsInventoryLotListResponse)
async def list_lots(
    db: SessionDep,
    _: ReadDep,
    material_code: str | None = Query(default=None, description="Exact material code"),
    mapped_status: str | None = Query(default=None, description="available | hold | expired"),
    warehouse_id: str | None = Query(default=None),
    erp_class_code: str | None = Query(
        default=None,
        description="ERP material class, e.g. 0102 Raw Ingredient, 02 Packaging Material"),
    search: str | None = Query(
        default=None,
        description="Case-insensitive substring of material code, material name, lot number or supplier batch"),
    expiring_before: date | None = Query(default=None),
    expiring_after: date | None = Query(default=None),
    aging_bucket: Literal["expired", "under_30", "30_to_60", "60_to_180", "over_180"] | None = Query(
        default=None,
        description="Restrict to one shelf-life band, resolved server-side from the same thresholds the summary uses"),
    sort: Literal["material_code", "lot_no", "expiry_date", "qty", "inbound_date"] = "material_code",
    descending: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """Browse and search inventory lots."""
    today = date.today()
    filters = dict(
        material_code=material_code, mapped_status=mapped_status,
        warehouse_id=warehouse_id, erp_class_code=erp_class_code, search=search,
        expiring_before=expiring_before, expiring_after=expiring_after,
        aging_bucket=aging_bucket, today=today, with_expiry_only=False,
    )

    count_stmt = _apply_lot_filters(
        select(func.count()).select_from(WmsInventoryLot).outerjoin(
            MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code),
        **filters)
    total = (await db.execute(count_stmt)).scalar_one()

    order = _SORTABLE[sort]
    stmt = _apply_lot_filters(_lots_joined_to_materials(), **filters)
    stmt = stmt.order_by(
        order.desc() if descending else order,
        # A stable tiebreak, so page 2 does not repeat a row from page 1 when
        # many lots share a sort value -- which they do: every lot of one
        # material sorts equal under the default.
        WmsInventoryLot.material_code, WmsInventoryLot.lot_no,
    ).offset((page - 1) * page_size).limit(page_size)

    items = []
    for lot, material in (await db.execute(stmt)).all():
        row = WmsInventoryLotResponse.model_validate(lot)
        row.material_name = material.name if material else None
        row.days_to_expiry = days_until(lot.expiry_date, today)
        row.aging_bucket = bucket_for(lot.expiry_date, today)
        items.append(row)

    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "as_of": today}


@router.get("/aging", response_model=AgingSummaryResponse)
async def aging_summary(
    db: SessionDep,
    _: ReadDep,
    erp_class_code: str | None = Query(
        default=None,
        description="Restrict to one material class, e.g. 0102 Raw Ingredient"),
    warehouse_id: str | None = Query(default=None),
):
    """Shelf life bucketed at 180 / 60 / 30 days, with expired as its own band.

    `today` is read once and passed into the bucketing, never called per row:
    two lots in one response must not land on opposite sides of midnight.
    """
    today = date.today()
    stmt = _apply_lot_filters(
        _lots_joined_to_materials(),
        material_code=None, mapped_status=None, warehouse_id=warehouse_id,
        erp_class_code=erp_class_code, search=None,
        expiring_before=None, expiring_after=None,
        aging_bucket=None, today=today, with_expiry_only=False,
    )
    lots = [lot for lot, _material in (await db.execute(stmt)).all()]
    summary = summarise(lots, today)

    return {
        "as_of": today,
        # Every bucket, in order, always -- a band missing from the payload
        # reads as "not computed" rather than "empty".
        "buckets": [
            {"key": key, "label": BUCKET_LABELS[key],
             "lots": summary.buckets[key].lots, "qty": summary.buckets[key].qty}
            for key in AGING_BUCKETS
        ],
        "no_expiry_lots": summary.no_expiry_lots,
        "no_expiry_qty": summary.no_expiry_qty,
        "erp_class_code": erp_class_code,
    }


@router.get("/materials", response_model=MaterialStockListResponse)
async def material_stock(
    db: SessionDep,
    _: ReadDep,
    search: str | None = Query(default=None, description="Material code or name"),
    erp_class_code: str | None = Query(default=None),
    only_with_stock: bool = Query(
        default=False, description="Hide materials with neither stock nor open orders"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """One row per material: what is here, and what is on the way.

    Stock and on-order come from different systems, so they are combined as a
    UNION of material codes rather than a join from either side. A material with
    orders and no stock — nothing in the warehouse, a delivery coming — is
    exactly the row a planner is looking for, and either join direction would
    drop one of the two cases.
    """
    today = date.today()

    stock_stmt = (
        select(
            WmsInventoryLot.material_code,
            func.sum(WmsInventoryLot.qty),
            func.sum(WmsInventoryLot.qty_allocated),
            func.sum(WmsInventoryLot.qty_onhold),
            # available = qty - qty_onhold over lots whose mapped_status is
            # 'available' (WMS 02/Release and not past date) -- the Phase 1
            # definition, unchanged, so this screen and the planning engine
            # cannot disagree about how much can actually be used.
            func.sum(case(
                (WmsInventoryLot.mapped_status == "available",
                 WmsInventoryLot.qty - WmsInventoryLot.qty_onhold),
                else_=0)),
            func.sum(case(
                (WmsInventoryLot.mapped_status == "expired", WmsInventoryLot.qty),
                else_=0)),
            func.min(WmsInventoryLot.expiry_date),
            func.count(),
        )
        .outerjoin(MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code)
        .where(_not_raw_milk())
        .group_by(WmsInventoryLot.material_code)
    )
    stock = {
        code: dict(on_hand=on_hand, allocated=allocated, on_hold=on_hold,
                   available=available, expired_qty=expired, next_expiry=next_expiry,
                   lots=lots)
        for code, on_hand, allocated, on_hold, available, expired, next_expiry, lots
        in (await db.execute(stock_stmt)).all()
    }
    on_order = await in_transit_by_material(db)

    # Names, units and classes for every code either side knows about.
    codes = set(stock) | set(on_order)
    materials = {
        m.code: m for m in (await db.execute(
            select(MdmMaterial).where(MdmMaterial.code.in_(codes)))).scalars()
    } if codes else {}

    rows: list[MaterialStockResponse] = []
    for code in sorted(codes):
        material = materials.get(code)
        if material is not None and material.erp_class_code == RAW_MILK_CLASS_CODE:
            continue
        if erp_class_code and (material is None or material.erp_class_code != erp_class_code):
            continue
        if search and search.strip():
            needle = search.strip().lower()
            name = (material.name or "") if material else ""
            if needle not in code.lower() and needle not in name.lower():
                continue

        s = stock.get(code, {})
        t = on_order.get(code)
        on_hand = s.get("on_hand") or Decimal("0")
        in_transit = t.qty if t else Decimal("0")
        if only_with_stock and on_hand == 0 and in_transit == 0:
            continue

        next_expiry = s.get("next_expiry")
        rows.append(MaterialStockResponse(
            material_code=code,
            material_name=material.name if material else None,
            base_uom=material.base_uom if material else None,
            erp_class_code=material.erp_class_code if material else None,
            erp_class_name=material.erp_class_name if material else None,
            on_hand=on_hand,
            available=s.get("available") or Decimal("0"),
            allocated=s.get("allocated") or Decimal("0"),
            on_hold=s.get("on_hold") or Decimal("0"),
            expired_qty=s.get("expired_qty") or Decimal("0"),
            next_expiry=next_expiry,
            days_to_next_expiry=days_until(next_expiry, today),
            lots=s.get("lots") or 0,
            # Zero, not null: "nothing is on order" is a fact, and a null here
            # would render as an empty cell that reads as "unknown".
            in_transit=in_transit,
            earliest_arrival=t.earliest_arrival if t else None,
            open_po_lines=t.open_lines if t else 0,
        ))

    total = len(rows)
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "total": total,
            "page": page, "page_size": page_size, "as_of": today}


@router.get("/materials/{material_code}/open-po-lines",
            response_model=list[OpenPoLineResponse])
async def material_open_po_lines(
    material_code: str, db: SessionDep, _: ReadDep,
):
    """The purchase order lines behind one material's in-transit figure.

    Applies exactly the same exclusions as the figure itself (they share
    `_open_line_filters` in `app/services/in_transit.py`) — otherwise a planner
    opens a 600 kg number and finds 2,600 kg of rows.
    """
    lines = await open_lines_for_material(db, material_code)
    return [OpenPoLineResponse(**vars(line)) for line in lines]
