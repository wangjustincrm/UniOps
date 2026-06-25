"""Pydantic schemas for Payment Application endpoints."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


PaStatus = Literal["draft", "submitted", "in_review", "approved", "processed", "returned", "cancelled", "paid"]
PaDocType = Literal["PA-PO", "PA-DIR"]


class PaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pa_number: str
    title: str
    pa_type: str          # 'regular' | 'prepayment' (EPMS legacy) or 'PA-PO' | 'PA-DIR' (OA)
    vendor_id: uuid.UUID
    vendor_name: str
    po_id: uuid.UUID | None
    po_number: str | None

    # Financial breakdown
    subtotal: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    other_charges: Decimal
    other_charges_note: str | None
    payment_amount: Decimal
    currency: str

    # Linked documents
    invoice_ids: list = []
    gr_ids: list = []
    budget_account_code: str | None
    cost_center_id: uuid.UUID | None = None

    status: str
    notes: str | None
    submitted_at: datetime | None
    approval_step_idx: int
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PaListResponse(BaseModel):
    items: list[PaResponse]
    total: int


class PaDirectCreate(BaseModel):
    """Create a PA-DIR (direct payment) linked to a reviewed expense invoice."""
    invoice_id: uuid.UUID
    vendor_id: uuid.UUID | None = None   # optional — matched later by Finance
    vendor_name: str
    payment_amount: Decimal
    currency: str = "CAD"
    notes: str | None = None
    title: str | None = None  # auto-generated from invoice if omitted
    budget_account_code: str | None = None
    # Cost center binding for the PA — needed since the L1/L2 catalog is now
    # shared across all cost centers (no longer per-CC). Used by /book-expense
    # in budget-api to identify which CC's plan + ledger this PA hits.
    cost_center_id: uuid.UUID | None = None


class PaDirectUpdate(BaseModel):
    """Partial update for a Draft/Returned PA-DIR — Payment-Details fields only."""
    title: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    budget_account_code: str | None = None
    cost_center_id: uuid.UUID | None = None
    notes: str | None = None


class PaActionRequest(BaseModel):
    action: Literal["submit", "approve", "return", "reject", "cancel", "recall"]
    comment: str | None = None


class PaymentRecord(BaseModel):
    bank_account_id: uuid.UUID   # funding bank/card chosen in the modal
