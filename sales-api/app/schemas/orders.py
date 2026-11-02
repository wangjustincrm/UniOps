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
    shipped_ea: int | None = None     # P5: from the 945
    delivered_qty: int | None = None  # P6: confirmed delivery (EA)


class OrderDocumentRead(BaseModel):
    """A source document, or an outbound 940 with its file and 997 status
    (kept apart: Sent is not Accepted, FR-OB-22)."""
    id: int
    kind: str
    direction: str
    remote_name: str | None
    created_at: datetime
    file_status: str | None = None
    ack_status: str | None = None
    st02: str | None = None
    sent_at: datetime | None = None
    error: str | None = None
    ack_detail: str | None = None


class TimelineEntry(BaseModel):
    at: datetime
    user_name: str
    entity: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None


class ShipmentRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lx_index: int
    w12_index: int
    sscc: str | None
    item_code: str | None
    lot: str | None
    expiry: date | None
    qty_ordered_ea: int | None
    qty_shipped_ea: int
    tracking: str | None
    needs_review: bool


class ShipmentRead(BaseModel):
    """A 945 as applied (or kept: a second 945, a shadow one), its rows and
    checks, and the Shopify write-back (FR-OB-11 / 25 / 25b / 25d)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    order_key_received: str | None
    ship_date: date | None
    ship_date_source: str | None
    carrier: str | None
    mode: str | None
    tracking: list[str] | None
    bol: str | None
    applied: bool
    shadow: bool
    checks: list[dict[str, Any]] | None
    shopify_status: str
    shopify_error: str | None
    rows: list[ShipmentRowRead] = []


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
    shipments: list[ShipmentRead] = []
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
