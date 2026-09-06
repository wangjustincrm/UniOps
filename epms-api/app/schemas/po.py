"""Pydantic schemas for Purchase Order (PO)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

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
    # False on a line a buyer added to a mirrored PO — see PoLineItem.nc_sourced.
    # The editor uses it to decide which rows it may write to.
    nc_sourced: bool = False
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


class PoManualLine(BaseModel):
    """A line a buyer added to an NC-imported PO.

    For a charge the ERP cannot carry: a one-off mould or tooling quote the
    supplier wants itemised on the PO they sign, with no material code and no
    place in NC's order.

    Deliberately absent, and each for its own reason:

    * ``material_id`` — a one-off charge has none, and a line that HAD one would
      start counting as MRP in-transit supply (in_transit.py takes every line
      whose material_id is not null). Unreachable rather than merely unset.
    * ``line_total`` — derived server-side from qty x unit_price. Accepting it
      would let the total disagree with its own factors, and it is the number
      that moves the PO header.
    * ``received_qty``, ``planned_arrival_date`` — receiving and ERP dates
      belong to NC's lines, not to a hand-added charge.

    An entry with no ``id`` is new; one with an ``id`` updates that line. This
    model REPLACES the line, so an omitted optional field clears it — unlike
    PoImportedLineUpdate, which patches two columns of an NC-owned line.
    """
    id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    # Zero or negative would be a line that means nothing; a negative PRICE is
    # a different matter and is allowed below (one-off credits are real).
    qty: Decimal = Field(gt=0)
    unit: str = Field(default="EA", max_length=30)
    unit_price: Decimal
    supplier_item_id: str | None = Field(default=None, max_length=100)
    sample: str | None = Field(default=None, max_length=100)

    @field_validator("description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description cannot be blank")
        return v.strip()


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
    # The COMPLETE set of buyer-added lines this PO should end up with — not a
    # patch. An entry with no id is created, one with an id is updated, and a
    # stored manual line absent from the list is deleted. NC-owned lines are
    # untouchable here: passing one of their ids is an error, not a shortcut.
    #
    # `None` (the key absent from the body) means "leave them alone", the same
    # absent-key contract every other field on this model follows — a save that
    # only changes Incoterms must not wipe the added lines. That is why it is
    # nullable rather than defaulting to an empty list, which would read as
    # "delete them all".
    manual_lines: list[PoManualLine] | None = None


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
    # Computed: this PO has an invoice a NEW payment could settle — unpaid AND
    # not already claimed by a live payment application. Both halves matter: an
    # invoice that is merely unpaid may already be locked to someone else's PA,
    # and offering the PO then leads to a payment screen with nothing to tick.
    # Filled in by BOTH the list and the detail endpoint (api/v1/po.py), off one
    # definition in crud.po::payable_invoice_po_ids.
    has_unpaid_invoice: bool = False
    # Computed (detail view): created_by of the linked PR — the requester who may
    # confirm delivery (create GR) on a service/project PO. None for direct POs.
    pr_requester_id: uuid.UUID | None = None
    # The department the PO's approvals route through (PO → PR.department_id).
    # Not a column on the PO — resolved per response in api/v1/po.py. NULL for a
    # PO with no PR (an NC-imported one), which is a distinct value, not
    # "unknown": approval routing falls back to the submitter's own department
    # there. The PA create/edit screens read it to keep a payment inside one
    # department, since a payment routes through its PRIMARY PO's department
    # only (approval-api engine.py::_routing_department_id).
    pr_department_id: uuid.UUID | None = None
    # Computed (detail view): ANY invoice points at this PO — not just an unpaid
    # one, and regardless of whether a PA already claims it. Distinct from
    # has_unpaid_invoice above. Drives the imported-PO editor's tax control:
    # once accounts payable is measuring against this header, its money must
    # stop moving.
    has_invoice: bool = False
    # 该 PO 被【其他发票】累计分摊的总额(所有 po_line_id 之和,仅 match-candidates 端点填充)
    already_allocated_total: Decimal | None = None
    current_step: CurrentStep | None = None
    # NC ERP provenance — 'nc' for POs mirrored from NC purchase orders, None for
    # POs created natively in UniOps. Read-only context surfaced on PO detail.
    source: str | None = None

    model_config = {"from_attributes": True}
