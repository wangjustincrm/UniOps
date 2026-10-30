"""Order DTOs: the order list and detail (read), review resolution and
manual orders (FR-OB-19a)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=20)]
Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: int
    partner_code: str
    source_type: str
    source_order_id: str
    source_order_number: str | None
    source_reference: str | None
    order_date: date | None
    requested_delivery_date: date | None
    ship_to: dict[str, Any] | None
    currency: str
    total: Decimal | None
    fulfillment_status: str
    invoice_status: str
    order_state: str = ""           # derived (D17), filled by the service
    hold_reason: str | None
    review_reason: str | None
    historical: bool
    first_received_at: datetime
    paid_at: datetime | None
    archived_in_shopify_at: datetime | None
    archive_suppressed: bool
    shopify_financial_status: str | None
    source_updated_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime | None


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    item_code: str | None
    source: dict[str, Any]
    ordered_qty: Decimal
    order_unit: str | None
    factor_ea: Decimal | None
    qty_ea: int | None
    unit_price: Decimal | None
    line_status: str
    removed: bool
    problem: str | None


class OrderDocumentRead(BaseModel):
    id: int
    kind: str
    remote_name: str | None
    created_at: datetime


class TimelineEntry(BaseModel):
    at: datetime
    user_name: str
    entity: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None


class OrderDetail(OrderRead):
    bill_to: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    notes: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    shipping: Decimal | None = None
    discount: Decimal | None = None
    department: str | None = None
    order_type: str | None = None
    lines: list[LineRead] = []
    documents: list[OrderDocumentRead] = []
    timeline: list[TimelineEntry] = []


class ManualLineIn(BaseModel):
    item_code: Code
    quantity: Qty
    unit: Unit


class ManualOrderIn(BaseModel):
    """A manual order (sample, phone order, staff purchase) for a MANUAL partner."""
    model_config = ConfigDict(extra="forbid")

    partner_code: Code
    customer_reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    requested_delivery_date: date | None = None
    ship_to: dict[str, Any] | None = None
    notes: Text | None = None
    lines: Annotated[list[ManualLineIn], Field(min_length=1, max_length=200)]


class LinePreview(BaseModel):
    item_code: str
    quantity: Decimal
    unit: str
    qty_ea: int | None
    problem: str | None


class ReviewResult(BaseModel):
    order: OrderRead
    remaining: list[str]
