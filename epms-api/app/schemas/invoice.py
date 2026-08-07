"""Pydantic schemas for Invoice."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InvoiceLineItem(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    unit: str | None = Field(default=None, max_length=50)
    # unit_price / line_total may be negative, mirroring PO lines: vendor invoices
    # carry the same discount / rebate / credit lines (header amount stays > 0).
    unit_price: Decimal = Field(default=Decimal("0"))
    line_total: Decimal = Field(default=Decimal("0"))
    # 非PO费用标记(shipping/packaging 等):不参与 PO 匹配,金额照付(随发票头)
    non_po_fee: bool = False
    non_po_note: str | None = Field(default=None, max_length=500)


class InvoiceCreate(BaseModel):
    vendor_id: uuid.UUID
    vendor_invoice_number: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="CAD", max_length=10)
    invoice_date: date
    due_date: date
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: str | None = Field(default=None, max_length=50)
    notes: str | None = None
    # Optional pre-link to a PO at upload time
    po_id: uuid.UUID | None = None


class InvoiceUpdate(BaseModel):
    vendor_invoice_number: str | None = Field(default=None, min_length=1, max_length=100)
    amount: Decimal | None = Field(default=None, gt=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=10)
    invoice_date: date | None = None
    due_date: date | None = None
    notes: str | None = None
    line_items: list[InvoiceLineItem] | None = None
    # Explicit GR selection for re-match; omit field entirely to keep existing GRs
    gr_ids: list[uuid.UUID] | None = None


class AllocationInput(BaseModel):
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None = None
    allocated_amount: Decimal = Field(ge=0)   # pre-tax
    allocated_tax: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None


class AllocationResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None
    allocated_amount: Decimal
    allocated_tax: Decimal
    allocated_total: Decimal
    variance: Decimal | None
    variance_pct: Decimal | None
    note: str | None
    # Display helpers resolved at read time (not stored on the allocation row).
    po_number: str | None = None
    po_line_description: str | None = None
    model_config = {"from_attributes": True}


class NonPoLineInput(BaseModel):
    line_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


class InvoiceMatchRequest(BaseModel):
    # New multi-PO path: when provided, takes priority.
    allocations: list[AllocationInput] | None = None
    # 非PO费用行:排除出匹配,但其 line_total 计入平账(照付)
    non_po_lines: list[NonPoLineInput] | None = None
    # Legacy single-PO path (existing tests / PATCH re-match / pre-rework UI).
    po_id: uuid.UUID | None = None
    gr_id: uuid.UUID | None = None
    gr_ids: list[uuid.UUID] | None = None
    po_line_ids: list[uuid.UUID] | None = None
    # When there are NO PO allocations (fee-only invoice, e.g. standalone freight),
    # the PO this invoice is associated with for traceability. Ignored when
    # allocations are present. The fees are paid in full via the AP header.
    reference_po_id: uuid.UUID | None = None
    # Agreement route: takes priority over every PO field when set. The invoice
    # is linked to the agreement for traceability and paid in full from the AP
    # header — there is no line reference to measure a variance against.
    agreement_id: uuid.UUID | None = None
    # 1A only: no pickup slips exist yet, so an agreement match is by definition
    # settled without receipt evidence and must record why.
    legacy_settlement_reason: str | None = None


class InvoiceExceptionRequest(BaseModel):
    resolution: str = Field(min_length=1, max_length=30)  # accepted | credit_note_requested
    note: str | None = None


class AssignMatchRequest(BaseModel):
    user_id: uuid.UUID


class DeclineMatchRequest(BaseModel):
    note: str


class MatchReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    internal_ref: str
    vendor_invoice_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    currency: str
    invoice_date: date
    due_date: date
    status: str
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    file_name: str | None
    file_size: str | None
    notes: str | None
    uploaded_by: uuid.UUID
    uploaded_by_name: str | None
    po_id: uuid.UUID | None
    po_number: str | None
    gr_id: uuid.UUID | None
    gr_number: str | None
    gr_ids: list | None = None
    matched_at: datetime | None
    matched_by: uuid.UUID | None
    matched_by_name: str | None
    po_total: Decimal | None
    gr_value: Decimal | None
    variance: Decimal | None
    variance_pct: Decimal | None
    matched_po_line_ids: list | None = None
    matched_reference_total: Decimal | None = None
    exception_reason: str | None
    exception_resolved_at: datetime | None
    exception_resolved_by: uuid.UUID | None
    exception_resolved_by_name: str | None
    exception_resolution: str | None
    created_at: datetime
    updated_at: datetime
    uploaded_at: datetime | None = None
    allocations: list[AllocationResponse] = Field(default_factory=list)
    match_assignee_id: uuid.UUID | None = None
    match_assignee_name: str | None = None
    agreement_id: uuid.UUID | None = None
    agreement_number: str | None = None
    match_route: str | None = None
    match_route_auto: bool = False
    legacy_settlement: bool = False
    legacy_settlement_reason: str | None = None

    @model_validator(mode='after')
    def _set_uploaded_at(self) -> 'InvoiceResponse':
        if self.uploaded_at is None:
            self.uploaded_at = self.created_at
        return self

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
