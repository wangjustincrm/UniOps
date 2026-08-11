"""Request/response schemas for house-account pickup slips (AGR § 小票)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, model_validator


def validate_totals(*, amount: Decimal, tax_amount: Decimal, total_amount: Decimal) -> None:
    """Shared by SlipCreate's schema validator and crud.agreement_slip.update()
    (post-merge, on the MERGED row) — same drift-avoidance rationale as
    agreement.py's validate_recurrence/validate_validity_window: two copies
    of this check could silently diverge. Three amounts are OCR-prefilled
    and all independently editable; a person changing one and forgetting
    another is the normal case, not the exception, and later reconciliation
    arithmetic reads total_amount, so an inconsistency here is not cosmetic.
    """
    if total_amount != amount + tax_amount:
        raise ValueError("total_amount must equal amount + tax_amount")


class SlipCreate(BaseModel):
    slip_date: date
    slip_ref: str | None = None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    picked_by: uuid.UUID
    missing_slip_reason: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _totals_are_consistent(self):
        validate_totals(
            amount=self.amount, tax_amount=self.tax_amount, total_amount=self.total_amount)
        return self


class SlipUpdate(BaseModel):
    slip_date: date | None = None
    slip_ref: str | None = None
    amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    picked_by: uuid.UUID | None = None
    missing_slip_reason: str | None = None
    notes: str | None = None

    # Deliberately NO totals validator here (unlike SlipCreate): a PATCH body
    # is partial and usually only touches one of the three amount fields, so
    # a validator that only sees `self` has nothing coherent to check against
    # (same reasoning as AgreementUpdate vs validate_recurrence in
    # schemas/agreement.py). The check instead runs in
    # crud.agreement_slip.update() against the MERGED post-patch row, via the
    # shared validate_totals() above.


class SlipResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    slip_date: date
    slip_ref: str | None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    picked_by: uuid.UUID
    missing_slip_reason: str | None
    ap_reviewed_by: uuid.UUID | None
    ap_reviewed_at: datetime | None
    status: str
    invoice_id: uuid.UUID | None
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SlipListResponse(BaseModel):
    items: list[SlipResponse]
    total: int


class SlipApReview(BaseModel):
    action: str   # approve | reject
