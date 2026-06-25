import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PaymentExecuteRequest(BaseModel):
    doc_kind: Literal["pa", "pa_dir", "expense_claim"]
    doc_id: uuid.UUID
    payment_date: date | None = None          # defaults to today
    payment_method: str = "bank_transfer"
    reference: str | None = None
    amount_paid: Decimal | None = None        # defaults to the document amount
    notes: str | None = None
    bank_account_id: uuid.UUID | None = None  # funding bank (records + GL cash account)


class PaymentExecuteResponse(BaseModel):
    doc_kind: str
    doc_id: uuid.UUID
    doc_number: str
    new_status: str
    payment_record_id: uuid.UUID
    posting_event_id: uuid.UUID | None
