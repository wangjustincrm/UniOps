import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel


class PaymentCreate(BaseModel):
    pa_id: uuid.UUID
    payment_date: date
    payment_method: str         # eft | cheque | wire | other
    reference: str | None = None
    amount: Decimal
    currency: str = "CAD"
    notes: str | None = None


class PaymentResponse(BaseModel):
    id: uuid.UUID
    pa_id: uuid.UUID
    pa_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    payment_date: date
    payment_method: str
    reference: str | None
    amount: Decimal
    currency: str
    status: str
    recorded_by: uuid.UUID
    notes: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int
