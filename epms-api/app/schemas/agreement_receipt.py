"""Request/response schemas for agreement receipts (AGR § 凭证).

A house_account agreement is not necessarily a counter-pickup account — it may
be a monthly delivery or an outsourced service instead. `receipt_type`
(counter_slip | delivery | service) is the discriminator; all three share the
same set of columns, and the difference is purely presentational (UI copy).
The legality check for `receipt_type` lives here, not on the ORM model —
deliberately no CHECK constraint on the table, matching this table's existing
`status` column convention (see app/models/agreement_receipt.py).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator, model_validator

_RECEIPT_TYPES = ("counter_slip", "delivery", "service")


def _validate_receipt_type(v: str | None) -> str | None:
    if v is not None and v not in _RECEIPT_TYPES:
        raise ValueError(
            "receipt_type must be one of: counter_slip, delivery, service")
    return v


def validate_totals(*, amount: Decimal, tax_amount: Decimal, total_amount: Decimal) -> None:
    """Shared by ReceiptCreate's schema validator and crud.agreement_receipt.update()
    (post-merge, on the MERGED row) — same drift-avoidance rationale as
    agreement.py's validate_recurrence/validate_validity_window: two copies
    of this check could silently diverge. Three amounts are OCR-prefilled
    and all independently editable; a person changing one and forgetting
    another is the normal case, not the exception, and later reconciliation
    arithmetic reads total_amount, so an inconsistency here is not cosmetic.
    """
    if total_amount != amount + tax_amount:
        raise ValueError("total_amount must equal amount + tax_amount")


class ReceiptCreate(BaseModel):
    # counter_slip | delivery | service。默认 counter_slip —— 绝大多数 house
    # account 仍是柜台领用,让最常见的情形免于每次都选。
    receipt_type: str = "counter_slip"
    receipt_date: date
    receipt_ref: str | None = None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    received_by: uuid.UUID
    missing_receipt_reason: str | None = None
    notes: str | None = None

    @field_validator("receipt_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        return _validate_receipt_type(v)

    @model_validator(mode="after")
    def _totals_are_consistent(self):
        validate_totals(
            amount=self.amount, tax_amount=self.tax_amount, total_amount=self.total_amount)
        return self


class ReceiptUpdate(BaseModel):
    receipt_type: str | None = None
    receipt_date: date | None = None
    receipt_ref: str | None = None
    amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    received_by: uuid.UUID | None = None
    missing_receipt_reason: str | None = None
    notes: str | None = None

    @field_validator("receipt_type")
    @classmethod
    def _known_type(cls, v: str | None) -> str | None:
        # None (field omitted or explicitly nulled) is let through on purpose —
        # a PATCH that doesn't touch receipt_type must not be forced to repeat
        # a valid value just to pass this check.
        return _validate_receipt_type(v)

    # Deliberately NO totals validator here (unlike ReceiptCreate): a PATCH body
    # is partial and usually only touches one of the three amount fields, so
    # a validator that only sees `self` has nothing coherent to check against
    # (same reasoning as AgreementUpdate vs validate_recurrence in
    # schemas/agreement.py). The check instead runs in
    # crud.agreement_receipt.update() against the MERGED post-patch row, via the
    # shared validate_totals() above.


class ReceiptResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    receipt_type: str
    receipt_date: date
    receipt_ref: str | None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    received_by: uuid.UUID
    missing_receipt_reason: str | None
    ap_reviewed_by: uuid.UUID | None
    ap_reviewed_at: datetime | None
    status: str
    invoice_id: uuid.UUID | None
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReceiptListResponse(BaseModel):
    items: list[ReceiptResponse]
    total: int


class ReceiptWithAgreementResponse(ReceiptResponse):
    """Same shape as ReceiptResponse plus the parent agreement's human number.

    Only used by the cross-agreement listing (GET /agreement-receipts) —
    the per-agreement listing (GET /agreements/{id}/receipts) already has the
    agreement in the URL, so plain ReceiptResponse is enough there. This
    listing has no such context, and the frontend must never render a bare
    agreement_id UUID (task-9 brief, item 5) — the number is what a human
    recognises.
    """
    agreement_number: str


class ReceiptListAllResponse(BaseModel):
    items: list[ReceiptWithAgreementResponse]
    total: int


class ReceiptApReview(BaseModel):
    action: str   # approve | reject
