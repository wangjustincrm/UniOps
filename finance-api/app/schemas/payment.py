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
    # Nullable on the table: expense-claim payments carry none of these.
    pa_id: uuid.UUID | None = None
    pa_number: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    doc_kind: str | None = None
    doc_number: str | None = None
    payee_name: str | None = None
    payment_date: date
    payment_method: str
    reference: str | None = None
    amount: Decimal
    currency: str
    status: str
    batch_id: uuid.UUID | None = None
    bank_account_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    recorded_by: uuid.UUID
    notes: str | None = None
    remittance_status: str | None = None     # sent | not_sent, filled by the API layer
    created_at: datetime
    model_config = {"from_attributes": True}


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int


class PaymentSummaryRow(BaseModel):
    currency: str
    count: int
    total: Decimal
