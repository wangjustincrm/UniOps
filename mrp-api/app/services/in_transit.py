"""What is on order and not yet in the warehouse.

Reads EPMS's purchase orders through the read-only mirror
(app/models/epms_mirror.py). Four exclusions, each load-bearing, each with the
number that justifies it in tests/test_in_transit.py:

**PO type must be 1** — raw material and packaging. Types 2-6 are consumables,
spare parts, service, fixed assets and software; 893 of the open lines
company-wide are those and not one carries a material code, so they could not be
attributed to a material even if they belonged here.

**Raw milk is never counted** (business rule, 2026-08-17). Identified by the
ERP/NC material classification `0101 Raw Milk` — 27 materials — never by code
prefix: `CR0059 Pasteurized Milk` is class 0101 while carrying an ordinary
raw-material prefix, so a prefix rule is wrong in both directions. Raw milk
arrives by tanker straight into production, is never warehoused as lots, and its
NC receipts do not reconcile against PO quantities.

**`nc_milk` POs are excluded too**, and this is not redundant with the rule
above: it is the arithmetic guard. Raw-milk receipt and stock-in happen in NC,
not UniOps, so `received_qty` is backfilled loosely and regularly EXCEEDS the
ordered quantity — CR0180 nets −1,648,350, CR0010 −1,525,432, all 238 such lines
−2,695,743. The classification rule is the business definition; this one keeps
any future non-0101 material on such a PO from contributing a negative
remainder.

**Only placed orders count.** draft / in_review / approved / rejected /
cancelled are not on their way anywhere: `approved` means somebody signed it,
not that a supplier has it.

Expected arrival prefers the line's own ERP date over the PO header's
hand-entered one, and is None when neither exists — never today, and never
derived from a lead time. "We do not know when this lands" is a fact a planner
can act on; a plausible invented date is not.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.epms_mirror import EpmsPoLineItem, EpmsPurchaseOrder, MdmMaterial

#: PO type carrying raw materials and packaging — the only type MRP plans.
RAW_MATERIAL_PO_TYPE = 1

#: Statuses meaning "the supplier has this order". Anything else is either not
#: yet sent or already finished.
PLACED_STATUSES = ("issued", "partially_received")

#: ERP/NC material class for raw milk, excluded from every stock and on-order
#: figure. See this module's docstring.
RAW_MILK_CLASS_CODE = "0101"

#: PO status the NC sync gives raw-milk orders, whose receipts are recorded
#: outside UniOps. Excluded as an arithmetic guard, see this module's docstring.
NC_MILK_STATUS = "nc_milk"


@dataclass(frozen=True)
class InTransit:
    """One material's open order position."""
    qty: Decimal
    earliest_arrival: date | None
    open_lines: int


@dataclass(frozen=True)
class OpenPoLine:
    """One open PO line, for the drill-down behind an in-transit figure."""
    po_number: str
    vendor_name: str
    material_code: str
    ordered: Decimal
    received: Decimal
    remaining: Decimal
    unit: str
    expected_arrival: date | None
    arrival_is_from_header: bool


def _expected_arrival():
    """The line's ERP date, else the PO header's hand-entered one.

    Coalesced in SQL so `min()` over a material's lines compares the same thing
    it displays. `expected_delivery` is empty on every NC-synced PO and
    `planned_arrival_date` is empty on every UniOps-native one, so in practice
    exactly one of the two is present per line — but the preference order is
    stated rather than assumed, because the line-level date is the ERP's and the
    header one is somebody's estimate.
    """
    return func.coalesce(
        EpmsPoLineItem.planned_arrival_date, EpmsPurchaseOrder.expected_delivery)


def _open_line_filters():
    """The single definition of "on order". Every query here spreads this, so
    the aggregate and the drill-down behind it cannot disagree about which lines
    they are describing."""
    return (
        EpmsPurchaseOrder.type == RAW_MATERIAL_PO_TYPE,
        EpmsPurchaseOrder.status.in_(PLACED_STATUSES),
        EpmsPurchaseOrder.status != NC_MILK_STATUS,
        EpmsPoLineItem.material_id.is_not(None),
        EpmsPoLineItem.qty > EpmsPoLineItem.received_qty,
        # coalesce, not `!= '0101'`: in SQL, NULL != '0101' is NULL, not true, so
        # a plain comparison would DROP every line whose material the master has
        # not caught up with. Those are real inbound quantities and losing them
        # understates what is coming. An unknown class is not raw milk.
        func.coalesce(MdmMaterial.erp_class_code, "") != RAW_MILK_CLASS_CODE,
    )


def _from_open_lines(*columns):
    """`select(...)` over open PO lines, joined to the material master.

    The material is LEFT-joined on purpose — see the coalesce note above.
    """
    return (
        select(*columns)
        .join(EpmsPurchaseOrder, EpmsPurchaseOrder.id == EpmsPoLineItem.po_id)
        .outerjoin(MdmMaterial, MdmMaterial.code == EpmsPoLineItem.material_id)
        .where(*_open_line_filters())
    )


async def in_transit_by_material(db: AsyncSession) -> dict[str, InTransit]:
    """Open order quantity per material code, with its earliest arrival."""
    arrival = _expected_arrival()
    stmt = _from_open_lines(
        EpmsPoLineItem.material_id,
        func.sum(EpmsPoLineItem.qty - EpmsPoLineItem.received_qty),
        func.min(arrival),
        func.count(),
    ).group_by(EpmsPoLineItem.material_id)
    return {
        code: InTransit(qty=qty, earliest_arrival=arrival_date, open_lines=lines)
        for code, qty, arrival_date, lines in (await db.execute(stmt)).all()
    }


async def open_lines_for_material(
    db: AsyncSession, material_code: str,
) -> list[OpenPoLine]:
    """The PO lines behind one material's in-transit figure, earliest first.

    Lines with no arrival date sort last: a planner reading this is asking "what
    lands next", and an unknown date is not an early one. (Postgres sorts NULLs
    last on an ascending order by default, which is the behaviour wanted here —
    stated because it is load-bearing, not incidental.)
    """
    arrival = _expected_arrival()
    stmt = _from_open_lines(
        EpmsPurchaseOrder.number,
        EpmsPurchaseOrder.vendor_name,
        EpmsPoLineItem.material_id,
        EpmsPoLineItem.qty,
        EpmsPoLineItem.received_qty,
        EpmsPoLineItem.unit,
        arrival,
        EpmsPoLineItem.planned_arrival_date,
    ).where(
        EpmsPoLineItem.material_id == material_code,
    ).order_by(arrival, EpmsPurchaseOrder.number)
    return [
        OpenPoLine(
            po_number=number,
            vendor_name=vendor_name,
            material_code=code,
            ordered=qty,
            received=received,
            remaining=qty - received,
            unit=unit,
            expected_arrival=expected,
            # Says WHOSE date it is. The ERP's line date is authoritative; the
            # header one was typed by a person, and a planner chasing a late
            # delivery deserves to know which they are looking at.
            arrival_is_from_header=expected is not None and line_date is None,
        )
        for number, vendor_name, code, qty, received, unit, expected, line_date
        in (await db.execute(stmt)).all()
    ]


async def raw_milk_material_codes(db: AsyncSession) -> set[str]:
    """Material codes MRP never counts as stock or as on order.

    Exposed so the inventory endpoints apply the same exclusion to WMS lots as
    this module applies to purchase orders, from one definition. Today the WMS
    mirror holds no raw-milk lots at all — raw milk goes straight into
    production — so this changes nothing yet, and that is precisely why it must
    be encoded rather than left to the fact that it currently does not matter.
    """
    stmt = select(MdmMaterial.code).where(
        MdmMaterial.erp_class_code == RAW_MILK_CLASS_CODE)
    return set((await db.execute(stmt)).scalars().all())
