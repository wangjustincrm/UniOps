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
    # Vendor credits to net off this payment. THREE-VALUED — compare with `is None`:
    #   None  -> apply the automatic FIFO default
    #   []    -> apply nothing this run
    #   [ids] -> apply only these; any that is no longer applicable aborts the payment
    credit_ids: list[uuid.UUID] | None = None
    # Which of a PA's invoices to pay. None -> everything still unpaid on it.
    invoice_ids: list[uuid.UUID] | None = None
    # What the caller expects a split payment to come to (a batch line's amount).
    # A different figure aborts the payment: part of the PA was paid since.
    expected_amount: Decimal | None = None


class PaymentExecuteResponse(BaseModel):
    doc_kind: str
    doc_id: uuid.UUID
    doc_number: str
    new_status: str
    payment_record_id: uuid.UUID
    posting_event_id: uuid.UUID | None
