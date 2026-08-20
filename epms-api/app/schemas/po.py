"""Pydantic schemas for Purchase Order (PO)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.current_step import CurrentStep

PO_STATUSES = {
    "draft", "submitted", "in_review", "approved",
    "returned", "rejected", "issued", "closed", "cancelled",
}

PO_WORKFLOW = [
    {"step": 0, "role": "procurement_manager", "label": "Procurement Manager"},
    {"step": 1, "role": "finance_manager",     "label": "Finance Manager"},
]

# Tax rates are no longer an enumerated set — they come from mdm-api tax_codes
# (Finance Tax Settings). PO validates only that the snapshot rate is a sane
# fraction (0 <= rate <= 1); the chosen code is recorded in tax_code.


# ── Line items ─────────────────────────────────────────────────────────────────

class PoLineItemIn(BaseModel):
    pr_line_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    material_id: str | None = Field(default=None, max_length=50)
    supplier_item_id: str | None = Field(default=None, max_length=100)
    qty: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)
    # unit_price may be 0 or negative: a discount / rebate / credit line carries a
    # negative price so its line_total nets down the order total (qty stays > 0).
    unit_price: Decimal
    notes: str | None = None

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))


class PoLineItemResponse(BaseModel):
    id: uuid.UUID
    pr_line_id: uuid.UUID | None
    description: str
    material_id: str | None
    supplier_item_id: str | None
    sample: str | None = None
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal
    received_qty: Decimal
    notes: str | None
    sort_order: int
    planned_arrival_date: date | None = None
    # 该 line 被【其他发票】累计分摊的税前额(仅 match-candidates 端点填充)
    already_allocated: Decimal | None = None

    model_config = {"from_attributes": True}


# ── PO ────────────────────────────────────────────────────────────────────────

class PoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    type: int = Field(ge=1, le=6)
    vendor_id: uuid.UUID
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    tax_code: str | None = Field(default=None, max_length=20)
    budget_code: str | None = Field(default=None, max_length=100)
    expected_delivery: date | None = None
    delivery_address: str | None = None
    notes: str | None = None
    pr_id: uuid.UUID | None = None
    is_prepaid: bool = False
    line_items: list[PoLineItemIn] = Field(default_factory=list)


class PoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    type: int | None = Field(default=None, ge=1, le=6)
    vendor_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, max_length=10)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    tax_code: str | None = Field(default=None, max_length=20)
    budget_code: str | None = Field(default=None, max_length=100)
    expected_delivery: date | None = None
    delivery_address: str | None = None
    notes: str | None = None
    is_prepaid: bool | None = None
    line_items: list[PoLineItemIn] | None = None


class PoImportedLineUpdate(BaseModel):
    """The only two line columns a buyer may fill in on an imported PO."""
    id: uuid.UUID
    supplier_item_id: str | None = Field(default=None, max_length=100)
    sample: str | None = Field(default=None, max_length=100)


class PoImportedDetailsUpdate(BaseModel):
    """Buyer-supplied detail on an NC-imported PO.

    Deliberately narrow. vendor_id, currency, title, budget_code, type and every
    line money/quantity field are absent, so no caller can reach them through
    this endpoint no matter what the frontend does or does not disable. Widening
    this model is a security change, not a convenience change.
    """
    expected_delivery: date | None = None
    delivery_address: str | None = None
    incoterms: str | None = Field(default=None, max_length=100)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    is_prepaid: bool | None = None
    buyer_notes: str | None = None
    lines: list[PoImportedLineUpdate] = Field(default_factory=list)


class PoListResponse(BaseModel):
    items: list["PoResponse"]
    total: int


class PoActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)
    comment: str | None = None


class PlaceOrderRequest(BaseModel):
    method: str = Field(pattern="^(email|online)$")
    # Email method fields
    to: str | None = Field(default=None, max_length=255)
    cc: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = None
    # Online method field
    reference: str | None = Field(default=None, max_length=255)


class PoResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    type: int
    status: str
    currency: str
    subtotal: Decimal
    tax_rate: Decimal
    tax_code: str | None
    tax_amount: Decimal
    total: Decimal
    vendor_id: uuid.UUID
    vendor_name: str
    is_prepaid: bool
    budget_code: str | None
    expected_delivery: date | None
    delivery_address: str | None
    notes: str | None
    # Buyer-supplied detail (NC-imported POs). `notes` stays NC-owned.
    buyer_notes: str | None = None
    incoterms: str | None = None
    buyer_edited_at: datetime | None = None
    approval_step_idx: int
    pr_id: uuid.UUID | None
    pr_number: str | None
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    line_items: list[PoLineItemResponse]
    # Place order tracking
    place_order_method: str | None = None
    place_order_email_to: str | None = None
    place_order_reference: str | None = None
    placed_at: datetime | None = None
    # Computed: True if at least one invoice for this PO is NOT yet paid
    has_unpaid_invoice: bool = False
    # Computed (detail view): created_by of the linked PR — the requester who may
    # confirm delivery (create GR) on a service/project PO. None for direct POs.
    pr_requester_id: uuid.UUID | None = None
    # 该 PO 被【其他发票】累计分摊的总额(所有 po_line_id 之和,仅 match-candidates 端点填充)
    already_allocated_total: Decimal | None = None
    current_step: CurrentStep | None = None
    # NC ERP provenance — 'nc' for POs mirrored from NC purchase orders, None for
    # POs created natively in UniOps. Read-only context surfaced on PO detail.
    source: str | None = None

    model_config = {"from_attributes": True}
