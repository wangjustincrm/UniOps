import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class PaymentCreate(BaseModel):
    pa_id: uuid.UUID
    payment_date: date
    payment_method: str         # eft | cheque | wire | other
    reference: str | None = None
    amount: Decimal
    currency: str = "CAD"
    notes: str | None = None


class AppliedCreditNoteOut(BaseModel):
    """One vendor credit note netted off this payment — the vendor's own
    number (never our internal credit_number) plus what THAT note
    contributed. A payment can net more than one; see PaymentResponse.credit_notes."""
    vendor_credit_number: str
    applied_amount: Decimal

    model_config = {"from_attributes": True}


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
    # `amount` is the CASH that left the bank — net of any vendor credit. The
    # gross the document asked for is `amount + credit_applied`; without that
    # second field a short payment is inexplicable anywhere inside the system
    # (the only other place it was ever stated is the remittance email to the
    # vendor). Defaults to 0 so payments recorded before Phase B, and
    # expense-claim payments (which never take credits), still validate.
    amount: Decimal
    credit_applied: Decimal = Decimal("0.00")
    # Which credit note(s) made up credit_applied — always empty pre-Phase-B
    # payments and expense-claim payments (which never take credits), always
    # populated by the API layer for the rest (see app/api/v1/payments.py).
    credit_notes: list[AppliedCreditNoteOut] = Field(default_factory=list)
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
