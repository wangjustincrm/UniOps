"""Warehouse receiving report — one row per physically received line item.

The warehouse has kept this by hand in a spreadsheet: every line that arrived,
what it was, which PO it came from, who received it, and how many days the
order took. This rebuilds that sheet from what the system already records.

Scope is deliberately narrow and matches the sheet it replaces:

* physical GRs only — a service confirmation has nothing to receive;
* NC-mirrored orders excluded (`purchase_orders.source = 'nc'`, which is every
  ERP-imported type-1 raw-material order). Those are received in NC and land
  here as a mirror, so they are not the warehouse's own receiving log;
* cancelled / rejected GRs excluded — they never represented received goods.

Dates are the plant's local dates, not UTC ones. `received_at` and friends are
stored as timestamptz and an 8pm Toronto receipt is already the next day in
UTC — reporting that would put the row on the wrong day and shift its lead time
by one, exactly the off-by-one this report is most likely to be checked for.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.delegation import PLANT_TIMEZONE
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User

_TZ = ZoneInfo(PLANT_TIMEZONE)

# Anything past this many rows is a range the browser cannot usefully render
# and a query nobody meant to run; the caller is told it was capped.
MAX_ROWS = 20_000


@dataclass(slots=True)
class ReceivingRow:
    """One received line, in the column order the warehouse's own sheet uses."""

    gr_id: uuid.UUID
    gr_number: str
    po_id: uuid.UUID
    po_number: str
    material_id: str | None
    description: str
    supplier: str
    unit: str
    quantity: Decimal
    department: str | None
    requested_by: str | None
    date_ordered: date | None
    arrival_date: date | None
    left_warehouse_date: date | None
    warehouse_receiver: str | None
    person_accepting: str | None
    lead_time_days: int | None


def _local_date(value: datetime | None) -> date | None:
    return value.astimezone(_TZ).date() if value else None


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_TZ)


async def receiving_rows(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    department_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    search: str | None = None,
    po_ids_subq=None,
) -> tuple[list[ReceivingRow], bool]:
    """Rows for the report, plus whether MAX_ROWS truncated the result."""

    # The PO's own "we placed this" stamp is only set by the Place Order action,
    # which about half the orders on file predate. Falling back to the last
    # approval — the point the order was cleared to go out — and then to the
    # creation date keeps a lead time on every row rather than blanking the
    # column for older orders. Newer orders take the first branch and are exact.
    last_approved_at = (
        select(func.max(ApprovalEvent.created_at))
        .where(
            ApprovalEvent.document_type == "po",
            ApprovalEvent.document_id == PurchaseOrder.id,
            ApprovalEvent.action == "approve",
        )
        .correlate(PurchaseOrder)
        .scalar_subquery()
    )
    ordered_at = func.coalesce(
        PurchaseOrder.placed_at, last_approved_at, PurchaseOrder.created_at
    )

    q = (
        select(
            GoodsReceipt.id,
            GoodsReceipt.number,
            GoodsReceipt.po_id,
            GoodsReceipt.po_number,
            GoodsReceipt.vendor_name,
            GoodsReceipt.received_at,
            GoodsReceipt.received_by,
            GoodsReceipt.collected_at,
            GoodsReceipt.collected_by,
            GrLineItem.description,
            # The GR line carries its own material id when the receiver typed
            # one; otherwise the ordered line's is the same part.
            func.coalesce(GrLineItem.material_id, PoLineItem.material_id).label("material_id"),
            GrLineItem.unit,
            GrLineItem.qty_received,
            GrLineItem.sort_order,
            PurchaseRequest.department_name,
            User.full_name,
            ordered_at.label("ordered_at"),
        )
        .select_from(GrLineItem)
        .join(GoodsReceipt, GoodsReceipt.id == GrLineItem.gr_id)
        .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.po_id)
        .outerjoin(PoLineItem, PoLineItem.id == GrLineItem.po_line_id)
        .outerjoin(PurchaseRequest, PurchaseRequest.id == GoodsReceipt.pr_id)
        .outerjoin(User, User.id == PurchaseRequest.created_by)
        .where(
            GoodsReceipt.gr_type == "physical",
            GoodsReceipt.status.not_in(("cancelled", "rejected")),
            PurchaseOrder.source.is_distinct_from("nc"),
        )
    )

    if po_ids_subq is not None:
        q = q.where(GoodsReceipt.po_id.in_(po_ids_subq))
    if date_from:
        q = q.where(GoodsReceipt.received_at >= _day_start(date_from))
    if date_to:
        # Inclusive of the whole local day, hence the next midnight.
        q = q.where(GoodsReceipt.received_at < _day_start(date_to + timedelta(days=1)))
    if department_id:
        q = q.where(PurchaseRequest.department_id == department_id)
    if vendor_id:
        q = q.where(GoodsReceipt.vendor_id == vendor_id)
    if search:
        term = f"%{search}%"
        q = q.where(
            or_(
                GoodsReceipt.number.ilike(term),
                GoodsReceipt.po_number.ilike(term),
                GoodsReceipt.vendor_name.ilike(term),
                GrLineItem.description.ilike(term),
                GrLineItem.material_id.ilike(term),
                PoLineItem.material_id.ilike(term),
                User.full_name.ilike(term),
            )
        )

    q = q.order_by(
        GoodsReceipt.received_at.asc(),
        GoodsReceipt.number.asc(),
        GrLineItem.sort_order.asc(),
    ).limit(MAX_ROWS + 1)

    records = (await db.execute(q)).all()
    truncated = len(records) > MAX_ROWS
    if truncated:
        records = records[:MAX_ROWS]

    rows: list[ReceivingRow] = []
    for r in records:
        arrival = _local_date(r.received_at)
        ordered = _local_date(r.ordered_at)
        rows.append(
            ReceivingRow(
                gr_id=r.id,
                gr_number=r.number,
                po_id=r.po_id,
                po_number=r.po_number,
                material_id=r.material_id,
                description=r.description,
                supplier=r.vendor_name,
                unit=r.unit,
                quantity=r.qty_received,
                department=r.department_name,
                requested_by=r.full_name,
                date_ordered=ordered,
                arrival_date=arrival,
                left_warehouse_date=_local_date(r.collected_at),
                warehouse_receiver=r.received_by,
                person_accepting=r.collected_by,
                lead_time_days=(arrival - ordered).days if arrival and ordered else None,
            )
        )
    return rows, truncated
