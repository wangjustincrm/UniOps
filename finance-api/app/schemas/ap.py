import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


class APPayableResponse(BaseModel):
    pa_id: uuid.UUID
    pa_number: str
    pa_type: str
    status: str
    vendor_id: uuid.UUID
    vendor_name: str
    po_number: str
    payment_amount: Decimal
    currency: str
    invoice_count: int
    submitted_at: datetime | None
    expected_settlement_date: str | None
    model_config = {"from_attributes": True}


class APPayableListResponse(BaseModel):
    items: list[APPayableResponse]
    total: int
