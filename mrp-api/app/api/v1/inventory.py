"""Inventory — what is in the warehouse, what is about to go out of date, and
what is on the way.

Three reads over the WMS lot mirror (`wms_inventory_lots`, a full-extract
snapshot replaced on every sync) joined to mdm's material master and, for the
on-order half, EPMS's purchase orders:

- `GET /inventory/batches` — stock grouped by SUPPLIER BATCH. What the screen
  shows: `lot_no` is Flux's internal identifier and means nothing outside the
  warehouse system, while the supplier batch is what a certificate of analysis
  carries and what somebody quotes on the phone. Grouping rather than merely
  hiding the column is not cosmetic — 2,143 of the 3,532 lots are visually
  identical to another lot once it is removed.
- `GET /inventory/batches/locations` — where one batch physically sits, for
  the expander under a batch row. A lot can occupy several locations (one is
  spread over 28), so this is a genuine second grain, not a column.
- `GET /inventory/lots` — the raw mirror, one row per WMS lot. Left in place
  for anyone who needs it; no screen uses it.
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
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, case, func, or_, select

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.epms_mirror import MdmMaterial
from app.models.status_mapping import MrpStatusMapping
from app.models.wms_inventory import WmsInventoryLot
from app.models.wms_lot_location import WmsLotLocation
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

#: The shelf-life horizon the summary warns on, asked for separately from the
#: aging bands (which are 30 / 60 / 180). Kept as a named constant so the
#: number on the screen and the number in the query cannot drift apart.
EXPIRY_WARNING_DAYS = 90

#: The warehouse's own "blocked" quality status (Flux QLT_STS 01 = Block).
#: NOT the same as our derived `hold`, which also covers 04 Under Inspection —
#: a summary that conflated them would report material as blocked when QA has
#: merely not finished looking at it.
BLOCKED_QUALITY_CODE = "01"

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


class BatchLocationResponse(BaseModel):
    """One place a batch physically sits.

    The grain is (location, handling unit): `STAGECANADA` holds 15 pallets of
    one lot, 700 each, distinguished by nothing but their trace id, so
    collapsing to location alone would report one 10,500 pallet that does not
    exist.
    """
    location_id: str
    #: From the warehouse's location master — the only human-meaningful thing
    #: it carries (there is no name or description column). Null when the
    #: location is not in the master, which does not stop it holding stock.
    zone_id: str | None
    #: The handling unit at that location. Null where the warehouse recorded
    #: none — the source writes '*', which is mapped here rather than shown.
    trace_id: str | None
    qty: Decimal
    qty_allocated: Decimal
    qty_onhold: Decimal
    #: The dates belong to the LOT sitting in this location, which is why they
    #: are here rather than on the batch summary: a batch spanning two
    #: production runs has two different answers and the summary row can only
    #: show one.
    production_date: date | None
    inbound_date: date | None
    expiry_date: date | None
    days_to_expiry: int | None
    quality_status: str | None
    quality_status_label: str | None
    mapped_status: str
    #: Present so a row can be traced back into Flux when somebody has to go
    #: and look at the physical pallet. Not shown by default.
    lot_no: str


class AgingBucket(BaseModel):
    key: str
    label: str
    #: WMS lots. Kept because it is the physical count, but the screen leads
    #: with `batches` -- that is the unit the list below it shows.
    lots: int
    #: Distinct supplier batches. 3,532 lots are only 877 batches, and one
    #: batch can hold 192 of them, so a card counting lots over a table
    #: listing batches is a discrepancy nobody can explain.
    batches: int
    qty: Decimal


class AgingSummaryResponse(BaseModel):
    as_of: date
    buckets: list[AgingBucket]
    #: Lots with no expiry date: excluded from every bucket, reported here.
    #: Mostly packaging, which does not expire.
    no_expiry_lots: int
    no_expiry_batches: int
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


class InventoryBatchResponse(BaseModel):
    """One SUPPLIER BATCH of one material in one warehouse.

    This, not the WMS lot, is the unit the plant works in: `lot_no` is Flux's
    internal identifier and means nothing outside the warehouse system, while
    the supplier batch is what appears on the certificate of analysis and what
    somebody quotes on the phone.
    """
    warehouse_id: str
    material_code: str
    material_name: str | None = None
    #: The unit the WAREHOUSE measures this in, carried on its own lots.
    #: ★ NOT the ERP's unit for the material: the ERP counts S0093 in PIECES
    #: because that is how it is sold, the warehouse weighs it in KG because
    #: that is how it is stored, and every quantity on this screen is the
    #: warehouse's. "mixed" if one batch's lots somehow disagree.
    base_uom: str | None = None
    #: None for stock the warehouse recorded without one (92 lots today).
    supplier_batch: str | None
    qty: Decimal
    qty_allocated: Decimal
    qty_onhold: Decimal
    #: How many WMS lots make up this batch. One supplier batch can be split
    #: across a great many — CP0080's "Old Wooden Racking Pallet" is 192.
    lots: int
    #: The EARLIEST expiry among this batch's lots: when it starts going out of
    #: date, which is the date somebody has to act on.
    expiry_date: date | None
    #: True when the batch's lots do not all share one expiry date (42 of the
    #: plant's 877 batches). Surfaced rather than averaged away — the single
    #: date above would otherwise quietly describe only part of the quantity.
    expiry_spans_dates: bool = False
    inbound_date: date | None
    days_to_expiry: int | None = None
    aging_bucket: str | None = None
    #: When the batch was produced — the earliest among its lots. Half the
    #: mirror has none (packaging is not produced in batches with dates); raw
    #: ingredients have it on every lot.
    production_date: date | None = None
    #: True when the batch's lots do not share one production date (29 of 877).
    production_spans_dates: bool = False
    #: The WAREHOUSE's quality status — raw QLT_STS from Flux: 02 Release,
    #: 01 Block, 04 Under Inspection — or "mixed" when the batch's lots
    #: disagree (29 of 877). Distinct from `mapped_status` below, which folds
    #: expiry in on top: 278 lots are Release in the warehouse and expired by
    #: date, and a screen showing only one of the two cannot say which.
    quality_status: str | None = None
    quality_status_label: str | None = None
    #: available | hold | expired | mixed — the warehouse status with expiry
    #: applied, which is what planning consumes.
    mapped_status: str
    supplier_code: str | None = None


class InventorySummaryLine(BaseModel):
    """Totals for one unit of measure over everything the current filters match.

    ★ One line PER UNIT, deliberately. The warehouse holds kilograms, pieces,
    each, rolls and centipoise, and packaging alone spans five of them — adding
    them together produces a number that describes nothing. Raw ingredients are
    all KGM, so the common view still shows a single line and reads like a plain
    total.

    The figures OVERLAP and are not a partition of the total: a blocked lot can
    also be expired, and an expiring one is still available today. They answer
    "how much of this is in that state", not "how does the total split".
    """
    uom: str | None
    total_qty: Decimal
    #: qty - qty_onhold over lots whose mapped_status is `available` — the same
    #: definition the Materials tab and the planning engine use.
    available_qty: Decimal
    expired_qty: Decimal
    #: The WAREHOUSE's block (QLT_STS 01), not our derived hold.
    blocked_qty: Decimal
    #: Not yet expired, but within the warning horizon.
    expiring_soon_qty: Decimal
    expiring_soon_batches: int
    batches: int
    lots: int


class InventoryBatchListResponse(BaseModel):
    items: list[InventoryBatchResponse]
    total: int
    page: int
    page_size: int
    as_of: date
    #: WMS lots behind the batches on this page — so the screen can say
    #: "128 batches (543 lots)" instead of leaving a reader to wonder where the
    #: lot count went.
    total_lots: int
    #: Totals over EVERY row the filters match, not just this page — and
    #: returned in the SAME response as the rows, not from a second endpoint.
    #: Two endpoints taking the same filters drift; one response cannot, and
    #: a headline disagreeing with the table under it is a discrepancy nobody
    #: can explain and nothing reports.
    summary: list[InventorySummaryLine]
    #: The horizon `expiring_soon_*` used, so the screen labels itself from the
    #: server rather than hardcoding a number that could fall out of step.
    expiry_warning_days: int


@router.get("/batches", response_model=InventoryBatchListResponse)
async def list_batches(
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
        description="Case-insensitive substring of material code, material name, supplier batch — or the internal WMS lot number, which is matched but never displayed"),
    expiring_before: date | None = Query(default=None),
    expiring_after: date | None = Query(default=None),
    aging_bucket: Literal["expired", "under_30", "30_to_60", "60_to_180", "over_180"] | None = Query(
        default=None,
        description="Restrict to one shelf-life band, resolved server-side from the same thresholds the summary uses"),
    sort: Literal["material_code", "supplier_batch", "production_date", "expiry_date", "qty", "inbound_date"] = "expiry_date",
    descending: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """Stock grouped by supplier batch — the list the Inventory screen shows.

    `GET /inventory/lots` still returns one row per WMS lot and is left alone
    for anyone who needs the raw mirror. This endpoint exists because that view
    is unreadable to a planner: **2,143 of the 3,532 lots are visually
    identical to another lot** once the internal lot number is removed, and one
    supplier batch can span 192 of them. Hiding the column without grouping
    would have produced page after page of repeated rows.

    Filters are applied to LOTS first and the survivors are then grouped, so
    `aging_bucket` selects the lots in a band and reports the batches they
    belong to — a batch whose lots carry different expiry dates appears in each
    band it genuinely has stock in, counted only for the lots that are there.
    """
    today = date.today()
    filters = dict(
        material_code=material_code, mapped_status=mapped_status,
        warehouse_id=warehouse_id, erp_class_code=erp_class_code, search=search,
        expiring_before=expiring_before, expiring_after=expiring_after,
        aging_bucket=aging_bucket, today=today, with_expiry_only=False,
    )

    grouping = (
        WmsInventoryLot.warehouse_id,
        WmsInventoryLot.material_code,
        WmsInventoryLot.supplier_batch,
    )

    base = select(
        *grouping,
        func.min(MdmMaterial.name),
        func.sum(WmsInventoryLot.qty),
        func.sum(WmsInventoryLot.qty_allocated),
        func.sum(WmsInventoryLot.qty_onhold),
        func.count(),
        func.min(WmsInventoryLot.expiry_date),
        func.max(WmsInventoryLot.expiry_date),
        func.min(WmsInventoryLot.inbound_date),
        # One status when the batch agrees with itself, "mixed" when it does
        # not. Picking the first would hide that part of a batch is on hold.
        case((func.count(func.distinct(WmsInventoryLot.mapped_status)) == 1,
              func.min(WmsInventoryLot.mapped_status)),
             else_="mixed"),
        func.min(WmsInventoryLot.supplier_code),
        case((func.count(func.distinct(WmsInventoryLot.uom)) == 1,
              func.min(WmsInventoryLot.uom)),
             else_="mixed"),
        func.min(WmsInventoryLot.production_date),
        func.max(WmsInventoryLot.production_date),
        # Same "mixed" rule as the derived status: picking one would hide that
        # part of the batch is blocked or still under inspection.
        case((func.count(func.distinct(WmsInventoryLot.wms_status)) == 1,
              func.min(WmsInventoryLot.wms_status)),
             else_="mixed"),
    ).outerjoin(MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code)
    base = _apply_lot_filters(base, **filters).group_by(*grouping)

    # count(*) over the grouped set — a plain count would count LOTS.
    grouped = base.subquery()
    total = (await db.execute(
        select(func.count()).select_from(grouped))).scalar_one()
    total_lots = (await db.execute(
        select(func.coalesce(func.sum(grouped.c[7]), 0)).select_from(grouped))).scalar_one()

    # Totals over the whole filtered set, computed from the same filters as the
    # rows above. Grouped by unit because summing across units is meaningless.
    warn_before = today + timedelta(days=EXPIRY_WARNING_DAYS)
    expiring_soon = (
        WmsInventoryLot.expiry_date.is_not(None)
        & (WmsInventoryLot.expiry_date > today)
        & (WmsInventoryLot.expiry_date < warn_before)
    )
    batch_key = func.concat(
        WmsInventoryLot.warehouse_id, "|", WmsInventoryLot.material_code, "|",
        func.coalesce(WmsInventoryLot.supplier_batch, ""))
    summary_stmt = _apply_lot_filters(
        select(
            WmsInventoryLot.uom,
            func.sum(WmsInventoryLot.qty),
            func.sum(case(
                (WmsInventoryLot.mapped_status == "available",
                 WmsInventoryLot.qty - WmsInventoryLot.qty_onhold), else_=0)),
            func.sum(case(
                (WmsInventoryLot.mapped_status == "expired", WmsInventoryLot.qty),
                else_=0)),
            func.sum(case(
                (WmsInventoryLot.wms_status == BLOCKED_QUALITY_CODE,
                 WmsInventoryLot.qty), else_=0)),
            func.sum(case((expiring_soon, WmsInventoryLot.qty), else_=0)),
            func.count(func.distinct(case((expiring_soon, batch_key)))),
            func.count(func.distinct(batch_key)),
            func.count(),
        ).outerjoin(MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code),
        **filters,
    ).group_by(WmsInventoryLot.uom).order_by(func.sum(WmsInventoryLot.qty).desc())

    summary = [
        InventorySummaryLine(
            uom=uom, total_qty=total_qty, available_qty=available,
            expired_qty=expired, blocked_qty=blocked,
            expiring_soon_qty=soon_qty, expiring_soon_batches=soon_batches,
            batches=batches, lots=lots,
        )
        for (uom, total_qty, available, expired, blocked, soon_qty,
             soon_batches, batches, lots) in (await db.execute(summary_stmt)).all()
    ]

    order_column = {
        "material_code": WmsInventoryLot.material_code,
        "supplier_batch": WmsInventoryLot.supplier_batch,
        "production_date": func.min(WmsInventoryLot.production_date),
        "expiry_date": func.min(WmsInventoryLot.expiry_date),
        "qty": func.sum(WmsInventoryLot.qty),
        "inbound_date": func.min(WmsInventoryLot.inbound_date),
    }[sort]
    stmt = base.order_by(
        order_column.desc() if descending else order_column,
        # A stable tiebreak: without it two batches sorting equal can swap
        # between queries and a row appears on two pages while another appears
        # on none.
        WmsInventoryLot.material_code, WmsInventoryLot.supplier_batch,
    ).offset((page - 1) * page_size).limit(page_size)

    # The warehouse's own quality-status dictionary (three rows), so the screen
    # shows "Release" rather than "02". Loaded once per request, never per row.
    labels = {
        code: description
        for code, description in (await db.execute(
            select(MrpStatusMapping.wms_code, MrpStatusMapping.description))).all()
    }

    items = []
    for (warehouse, code, batch, name, qty_, allocated, onhold, lot_count,
         expiry_min, expiry_max, inbound, status, supplier_code,
         base_uom, produced_min, produced_max, quality) in (
            await db.execute(stmt)).all():
        items.append(InventoryBatchResponse(
            warehouse_id=warehouse,
            material_code=code,
            material_name=name,
            base_uom=base_uom,
            supplier_batch=batch,
            qty=qty_,
            qty_allocated=allocated,
            qty_onhold=onhold,
            lots=lot_count,
            expiry_date=expiry_min,
            expiry_spans_dates=expiry_min != expiry_max,
            inbound_date=inbound,
            days_to_expiry=days_until(expiry_min, today),
            aging_bucket=bucket_for(expiry_min, today),
            production_date=produced_min,
            production_spans_dates=(
                produced_min is not None and produced_min != produced_max),
            quality_status=quality,
            # An unknown code shows as the code itself rather than as blank:
            # a status nobody can read still beats a status nobody can see.
            quality_status_label=(
                "Mixed" if quality == "mixed" else labels.get(quality, quality)),
            mapped_status=status,
            supplier_code=supplier_code,
        ))

    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "as_of": today, "total_lots": total_lots,
            "summary": summary, "expiry_warning_days": EXPIRY_WARNING_DAYS}


@router.get("/batches/locations", response_model=list[BatchLocationResponse])
async def batch_locations(
    db: SessionDep,
    _: ReadDep,
    material_code: str = Query(description="The batch's material"),
    supplier_batch: str | None = Query(
        default=None,
        description="The supplier batch. Omit for the batch of stock that has none — 92 lots carry no supplier batch, and they are a real row on the list."),
    warehouse_id: str | None = Query(default=None),
):
    """Where one supplier batch physically sits, fullest location first.

    Sorted by quantity rather than by location code because the question behind
    the click is "where is most of it", and a code sort would put a 10 kg
    remnant above a full pallet.
    """
    today = date.today()
    stmt = (
        select(WmsLotLocation, WmsInventoryLot)
        .join(
            WmsInventoryLot,
            (WmsInventoryLot.warehouse_id == WmsLotLocation.warehouse_id)
            & (WmsInventoryLot.material_code == WmsLotLocation.material_code)
            & (WmsInventoryLot.lot_no == WmsLotLocation.lot_no),
        )
        .where(
            WmsLotLocation.material_code == material_code,
            # `is_(None)` rather than `== None`: SQL equality against NULL is
            # never true, so the 92 lots with no supplier batch would return an
            # empty expander that reads as "we do not know where this is".
            WmsInventoryLot.supplier_batch.is_(None) if supplier_batch is None
            else WmsInventoryLot.supplier_batch == supplier_batch,
        )
        .order_by(WmsLotLocation.qty.desc(), WmsLotLocation.location_id)
    )
    if warehouse_id:
        stmt = stmt.where(WmsLotLocation.warehouse_id == warehouse_id)

    labels = {
        code: description
        for code, description in (await db.execute(
            select(MrpStatusMapping.wms_code, MrpStatusMapping.description))).all()
    }

    return [
        BatchLocationResponse(
            location_id=location.location_id,
            zone_id=location.zone_id,
            # '*' is the source's way of writing "none"; null renders as a
            # dash instead of as a character nobody can interpret.
            trace_id=None if location.trace_id == "*" else location.trace_id,
            qty=location.qty,
            qty_allocated=location.qty_allocated,
            qty_onhold=location.qty_onhold,
            production_date=lot.production_date,
            inbound_date=lot.inbound_date,
            expiry_date=lot.expiry_date,
            days_to_expiry=days_until(lot.expiry_date, today),
            quality_status=lot.wms_status,
            quality_status_label=labels.get(lot.wms_status, lot.wms_status),
            mapped_status=lot.mapped_status,
            lot_no=lot.lot_no,
        )
        for location, lot in (await db.execute(stmt)).all()
    ]


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
             "lots": summary.buckets[key].lots,
             "batches": summary.buckets[key].batches,
             "qty": summary.buckets[key].qty}
            for key in AGING_BUCKETS
        ],
        "no_expiry_lots": summary.no_expiry_lots,
        "no_expiry_batches": summary.no_expiry_batches,
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
            # The warehouse's unit, not the ERP's — these are the warehouse's
            # quantities. See WmsInventoryLot.uom.
            case((func.count(func.distinct(WmsInventoryLot.uom)) == 1,
                  func.min(WmsInventoryLot.uom)),
                 else_="mixed"),
        )
        .outerjoin(MdmMaterial, MdmMaterial.code == WmsInventoryLot.material_code)
        .where(_not_raw_milk())
        .group_by(WmsInventoryLot.material_code)
    )
    stock = {
        code: dict(on_hand=on_hand, allocated=allocated, on_hold=on_hold,
                   available=available, expired_qty=expired, next_expiry=next_expiry,
                   lots=lots, uom=uom)
        for code, on_hand, allocated, on_hold, available, expired, next_expiry, lots, uom
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
            # The warehouse's unit where there is stock; the ERP's only as a
            # last resort, for a material that is purely on order and has no
            # lot to take a unit from. The in-transit quantity is in the PO
            # line's own unit, which the drill-down shows per line.
            base_uom=s.get("uom") or (material.base_uom if material else None),
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
