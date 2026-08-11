"""Request/response schemas for house-account pickup slips (AGR § 小票)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, model_validator


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
        # 三个金额都由 OCR 预填且都可编辑,人改了一个忘了另一个是常态;
        # 不校验的话对账差额会莫名其妙。
        if self.total_amount != self.amount + self.tax_amount:
            raise ValueError("total_amount must equal amount + tax_amount")
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
