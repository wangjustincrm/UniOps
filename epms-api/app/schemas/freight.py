"""Pydantic schemas for courier freight payment (Freight)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChargeType = Literal["shipment", "pickup", "fee", "adjustment"]
Direction = Literal["outbound", "inbound"]


# ── Carriers ──────────────────────────────────────────────────────────────────

class CarrierCreate(BaseModel):
    vendor_id: uuid.UUID
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    tracking_pattern: str | None = Field(default=None, max_length=200)
    default_cost_center_id: uuid.UUID | None = None


class CarrierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    tracking_pattern: str | None = Field(default=None, max_length=200)
    default_cost_center_id: uuid.UUID | None = None
    is_active: bool | None = None


class CarrierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str | None = None
    code: str
    name: str
    tracking_pattern: str | None
    default_cost_center_id: uuid.UUID | None
    is_active: bool


# ── Shipments ─────────────────────────────────────────────────────────────────

class ShipmentCreate(BaseModel):
    carrier_id: uuid.UUID
    tracking_no: str = Field(min_length=1, max_length=64)
    cost_center_id: uuid.UUID
    direction: Direction = "outbound"
    note: str | None = None
    attachment_key: str | None = Field(default=None, max_length=500)


class ShipmentUpdate(BaseModel):
    tracking_no: str | None = Field(default=None, min_length=1, max_length=64)
    cost_center_id: uuid.UUID | None = None
    direction: Direction | None = None
    note: str | None = None


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    carrier_id: uuid.UUID
    carrier_code: str | None = None
    tracking_no: str
    direction: str
    ship_date: date | None
    cost_center_id: uuid.UUID
    cost_center_code: str | None = None
    cost_center_name: str | None = None
    department_id: uuid.UUID
    department_name: str | None = None
    note: str | None
    attachment_key: str | None
    source: str
    status: str
    created_by: uuid.UUID
    created_by_name: str | None
    created_at: datetime
    # The invoice that billed it, when billed.
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    billed_amount: Decimal | None = None


class ShipmentListResponse(BaseModel):
    items: list[ShipmentResponse]
    total: int


class DefaultCostCenterResponse(BaseModel):
    cost_center_id: uuid.UUID | None
    # single | freight_budget | none — why this default was chosen
    reason: str
    # Cost centres of this department whose current-year plan carries the freight account.
    budgeted_cost_center_ids: list[uuid.UUID] = []


# ── Invoices ──────────────────────────────────────────────────────────────────

class FreightLineIn(BaseModel):
    charge_type: ChargeType = "shipment"
    tracking_no: str | None = Field(default=None, max_length=64)
    package_tracking_nos: list[str] = []
    reference: str | None = Field(default=None, max_length=200)
    ship_date: date | None = None
    description: str | None = Field(default=None, max_length=500)
    amount: Decimal
    tax_amount: Decimal = Decimal("0")


class FreightInvoiceCreate(BaseModel):
    carrier_id: uuid.UUID
    vendor_invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    due_date: date
    currency: str = "CAD"
    amount: Decimal              # pre-tax, THIS period only
    tax_amount: Decimal = Decimal("0")
    file_name: str | None = None
    file_size: str | None = None
    notes: str | None = None
    lines: list[FreightLineIn] = Field(min_length=1)


class FreightLineUpdate(BaseModel):
    """Human decision on one line. Any field sent here marks the line manual."""
    cost_center_id: uuid.UUID | None = None
    shipment_id: uuid.UUID | None = None
    linked_line_id: uuid.UUID | None = None
    # Explicitly clear the shipment / linked line (None above means "unchanged").
    clear_shipment: bool = False
    clear_link: bool = False
    tax_amount: Decimal | None = None
    amount: Decimal | None = None
    tracking_no: str | None = None
    package_tracking_nos: list[str] | None = None


class FreightLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sort_order: int
    charge_type: str
    tracking_no: str | None
    package_tracking_nos: list[str]
    reference: str | None
    ship_date: date | None
    description: str | None
    amount: Decimal
    tax_amount: Decimal
    shipment_id: uuid.UUID | None
    linked_line_id: uuid.UUID | None
    cost_center_id: uuid.UUID | None
    cost_center_code: str | None = None
    cost_center_name: str | None = None
    department_name: str | None = None
    match_status: str
    # For duplicate_billing: where the shipment was billed before.
    previously_billed_on: str | None = None


class CostCenterTotal(BaseModel):
    cost_center_id: uuid.UUID | None
    cost_center_code: str | None
    cost_center_name: str | None
    department_name: str | None
    amount: Decimal
    tax_amount: Decimal


class FreightInvoiceResponse(BaseModel):
    id: uuid.UUID
    internal_ref: str
    carrier_id: uuid.UUID
    carrier_code: str | None
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    file_name: str | None
    notes: str | None
    uploaded_by_name: str | None
    created_at: datetime
    lines: list[FreightLineResponse] = []
    totals_by_cost_center: list[CostCenterTotal] = []
    lines_amount_total: Decimal = Decimal("0")
    lines_tax_total: Decimal = Decimal("0")
    # PA that currently holds this invoice (any status except cancelled).
    pa_id: uuid.UUID | None = None
    pa_number: str | None = None
    # Why finalize would be refused right now; empty when it can go.
    finalize_blockers: list[str] = []


class FreightInvoiceListItem(BaseModel):
    id: uuid.UUID
    internal_ref: str
    carrier_id: uuid.UUID
    carrier_code: str | None
    vendor_invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    line_count: int
    pa_id: uuid.UUID | None = None
    pa_number: str | None = None
    pa_status: str | None = None


class FreightInvoiceListResponse(BaseModel):
    items: list[FreightInvoiceListItem]
    total: int


class DuplicateCheckResponse(BaseModel):
    duplicate: bool
    detail: str | None = None


# ── PA ────────────────────────────────────────────────────────────────────────

class FreightPaCreate(BaseModel):
    invoice_ids: list[uuid.UUID] = Field(min_length=1)
    title: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class FreightPaCreated(BaseModel):
    id: uuid.UUID
    pa_number: str
