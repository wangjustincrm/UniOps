"""Pydantic schemas for Payment Application (PA)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

PA_WORKFLOW = [
    {"step": 0, "role": "finance_bp",      "label": "Finance BP Review"},
    {"step": 1, "role": "finance_manager", "label": "Finance Manager Approval"},
]


# ── Line items ─────────────────────────────────────────────────────────────────

class PaLineItemIn(BaseModel):
    po_line_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    qty: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)
    unit_price: Decimal = Field(ge=0)
    notes: str | None = None

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))


class PaLineItemResponse(BaseModel):
    id: uuid.UUID
    po_line_id: uuid.UUID | None
    description: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal
    notes: str | None
    sort_order: int

    model_config = {"from_attributes": True}


# ── PA ────────────────────────────────────────────────────────────────────────

class PaCreate(BaseModel):
    po_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    pa_type: str = Field(default="regular", max_length=20)  # regular | prepayment | settlement | balance
    prepayment_pa_id: uuid.UUID | None = None   # required for settlement / balance types
    prepayment_applied: Decimal | None = Field(default=None, ge=0)  # settlement/balance 抵扣的预付额
    invoice_ids: list[uuid.UUID] = Field(default_factory=list)
    gr_ids: list[uuid.UUID] = Field(default_factory=list)
    subtotal: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    shipping_amount: Decimal = Field(default=Decimal("0"), ge=0)
    other_charges: Decimal = Field(default=Decimal("0"), ge=0)
    other_charges_note: str | None = Field(default=None, max_length=255)
    currency: str = Field(default="CAD", max_length=10)
    notes: str | None = None
    prepayment_pct: Decimal | None = Field(default=None, ge=1, le=100)
    expected_settlement_date: date | None = None
    line_items: list[PaLineItemIn] = Field(default_factory=list)


class PaUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    invoice_ids: list[uuid.UUID] | None = None
    gr_ids: list[uuid.UUID] | None = None
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    shipping_amount: Decimal | None = Field(default=None, ge=0)
    other_charges: Decimal | None = Field(default=None, ge=0)
    other_charges_note: str | None = None
    notes: str | None = None
    prepayment_pct: Decimal | None = Field(default=None, ge=1, le=100)
    prepayment_applied: Decimal | None = Field(default=None, ge=0)
    expected_settlement_date: date | None = None
    line_items: list[PaLineItemIn] | None = None


class PaActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)
    comment: str | None = None
    bank_account_id: uuid.UUID | None = None   # funding bank/card for action='process'


class PaSettleRequest(BaseModel):
    note: str | None = None
    variance: Decimal = Field(default=Decimal("0"))
    resolution: str = Field(default="settled", max_length=20)  # settled | disputed


class PaResponse(BaseModel):
    id: uuid.UUID
    pa_number: str
    title: str
    po_id: uuid.UUID
    po_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    invoice_ids: list
    gr_ids: list
    pa_type: str
    prepayment_pa_id: uuid.UUID | None
    prepayment_applied: Decimal | None
    subtotal: Decimal
    tax_amount: Decimal
    tax_code: str | None
    tax_rate: Decimal | None
    shipping_amount: Decimal
    other_charges: Decimal
    other_charges_note: str | None
    payment_amount: Decimal
    currency: str
    status: str
    notes: str | None
    submitted_at: datetime | None
    prepayment_pct: Decimal | None
    expected_settlement_date: date | None
    settlement_status: str | None
    settled_at: datetime | None
    settled_by: uuid.UUID | None
    settled_by_name: str | None
    settlement_note: str | None
    settlement_variance: Decimal | None
    approval_step_idx: int
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    line_items: list[PaLineItemResponse]

    model_config = {"from_attributes": True}


class PaListResponse(BaseModel):
    items: list[PaResponse]
    total: int
