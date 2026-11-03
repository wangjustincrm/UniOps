"""Invoicing DTOs (FR-OB-19, 30 … 35)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    order_id: int
    method: str
    invoice_date: date
    currency: str
    payment_terms: str | None
    subtotal: Decimal
    total: Decimal
    document_id: int | None
    sent_at: datetime | None
    send_count: int
    error: str | None
    version: int
    created_at: datetime
    updated_by_name: str | None = None
    # from the order (filled by the service)
    order_number: int | None = None
    partner_code: str | None = None
    po: str | None = None
    order_ref: str | None = None      # the order's ref (A.3)
    partner_name: str | None = None
    delivery_number: str | None = None
    status: str | None = None


class InvoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    item_code: str | None
    buyer_part: str | None
    description: str | None
    uom: str
    qty: int
    unit_price: Decimal
    amount: Decimal
    # The price the PO gives for one invoice unit (CASE): 850 price × CASE factor ÷ the
    # order unit's factor, from the order line now (read only; filled by the service).
    po_price: Decimal | None = None


class InvoiceDetail(InvoiceRead):
    lines: list[InvoiceLineRead] = []
    confirmed_date: date | None = None


class DeliveredLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_line_id: int
    delivered_qty: Annotated[int, Field(ge=0)]


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    confirmed_date: date
    lines: list[DeliveredLine] = []     # empty: delivered = shipped
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


class ConfirmBulkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_ids: Annotated[list[int], Field(min_length=1, max_length=200)]
    confirmed_date: date


class ConfirmResult(BaseModel):
    order_id: int
    ok: bool
    invoice_id: int | None = None
    status: str | None = None
    error: str | None = None


class EditLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    qty: Annotated[int, Field(ge=0)] | None = None
    unit_price: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=5)] | None = None


class InvoiceEdit(BaseModel):
    """`delivery_number` set (even to null) changes it; left out keeps it. A
    changed unit price needs `reason` (≥ 3 characters), kept in the audit."""
    model_config = ConfigDict(extra="forbid")
    invoice_date: date | None = None
    delivery_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    lines: list[EditLine] = []


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    reason: Reason


class ReturnLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_line_id: int
    qty_ea: Annotated[int, Field(gt=0)]


class ReturnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    kind: Literal["Cancelled after shipment", "Returned"]
    reason: Reason
    lines: Annotated[list[ReturnLine], Field(min_length=1)]
