"""Pydantic schemas for Payment Application (PA)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas.current_step import CurrentStep

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
    # unit_price may be 0 or negative, mirroring PO lines: a discount / rebate /
    # credit line carries a negative price (header subtotal stays >= 0).
    unit_price: Decimal
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
    # The PRIMARY purchase order. Kept for callers (and stored history) that
    # know only one PO; `po_ids` is the full set and always wins when given.
    po_id: uuid.UUID | None = None
    # One PA may settle several POs of the same vendor in one payment — the
    # ordinary AP case of one cheque covering three orders. Order is meaningful:
    # the first entry becomes the primary PO mirrored onto the PA header.
    po_ids: list[uuid.UUID] = Field(default_factory=list)
    # Agreement-sourced PA (no PO, no GR). Exactly one of po_id / agreement_id
    # must be set — a PA with neither is OA's Direct PA, which EPMS does not own.
    agreement_id: uuid.UUID | None = None
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
    receipt_override: bool = False
    receipt_override_reason: str | None = Field(default=None, max_length=500)

    @property
    def resolved_po_ids(self) -> list[uuid.UUID]:
        """The PA's purchase orders, de-duplicated, order preserved.

        `po_ids` wins when present; a caller that only sent `po_id` (the old
        single-PO body, and everything already in the wild) resolves to a
        one-element list, so the endpoint below has exactly one shape to reason
        about. Empty on the agreement route.
        """
        raw = self.po_ids or ([self.po_id] if self.po_id is not None else [])
        seen: set[uuid.UUID] = set()
        out: list[uuid.UUID] = []
        for pid in raw:
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out

    @model_validator(mode="after")
    def _exactly_one_source(self):
        # Normalize before the XOR so `po_ids=[x]` alone is as valid as
        # `po_id=x` alone, and the two agreeing is not an error.
        pos = self.po_ids or ([self.po_id] if self.po_id is not None else [])
        if (not pos) == (self.agreement_id is None):
            raise ValueError("Provide exactly one of po_id/po_ids or agreement_id")
        if pos and self.po_id is not None and self.po_id not in pos:
            raise ValueError("po_id must be one of po_ids")
        return self


class PaUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    # Replaces the PA's whole PO set (draft / returned only, PO route only).
    # None = leave the POs alone; an empty list is rejected by the endpoint —
    # a PO-route PA with no PO would become indistinguishable from OA's Direct
    # PA and drop out of every EPMS list.
    po_ids: list[uuid.UUID] | None = None
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
    # Vendor credits to net off this payment (action='process' only).
    # THREE-VALUED — compare with `is None`, exactly as finance-api's
    # PaymentExecuteRequest.credit_ids does:
    #   None  -> apply finance-api's automatic FIFO default
    #   []    -> apply nothing this run
    #   [ids] -> apply only these
    # The Process dialog sends this ONLY when the operator deselected a credit
    # the preview offered; an untouched dialog leaves it absent so the server
    # keeps choosing.
    credit_ids: list[uuid.UUID] | None = None


class PaSettleRequest(BaseModel):
    note: str | None = None
    variance: Decimal = Field(default=Decimal("0"))
    resolution: str = Field(default="settled", max_length=20)  # settled | disputed


class PaResponse(BaseModel):
    id: uuid.UUID
    pa_number: str
    title: str
    po_id: uuid.UUID | None
    po_number: str | None
    # Every PO this PA pays, primary first. Populated from pa_po_links by
    # crud.pa (see attach_po_links); [] on the agreement route. po_id/po_number
    # above stay the primary PO so older clients keep rendering something.
    po_ids: list[uuid.UUID] = Field(default_factory=list)
    po_numbers: list[str] = Field(default_factory=list)
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
    receipt_override: bool = False
    receipt_override_reason: str | None = None
    receipt_override_by: uuid.UUID | None = None
    current_step: CurrentStep | None = None
    agreement_id: uuid.UUID | None = None
    agreement_number: str | None = None

    model_config = {"from_attributes": True}


class PaListResponse(BaseModel):
    items: list[PaResponse]
    total: int
