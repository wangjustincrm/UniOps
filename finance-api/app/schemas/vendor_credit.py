"""Pydantic contracts for Vendor Credit.

Note the deliberate absence of a positivity constraint on `amount`: callers
legitimately submit what the vendor printed, which is usually negative. The
CRUD layer is the single place that normalises sign (see app/crud/vendor_credit.py).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class VendorCreditCreate(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str = Field(min_length=1, max_length=255)
    vendor_credit_number: str = Field(min_length=1, max_length=100)
    credit_date: date
    currency: str = Field(default="CAD", max_length=10)
    amount: Decimal                                   # pre-tax, sign-agnostic
    tax_amount: Decimal = Decimal("0")                # sign-agnostic
    po_id: uuid.UUID | None = None
    po_number: str | None = Field(default=None, max_length=40)
    line_items: list[dict] = Field(default_factory=list)
    file_name: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class VendorCreditReview(BaseModel):
    note: str | None = None


class VendorCreditReject(BaseModel):
    note: str = Field(min_length=1)


class VendorCreditResponse(BaseModel):
    id: uuid.UUID
    credit_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_credit_number: str
    credit_date: date
    currency: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    applied_amount: Decimal
    remaining_amount: Decimal
    status: str
    po_id: uuid.UUID | None
    po_number: str | None
    line_items: list
    file_name: str | None
    notes: str | None
    source: str
    opening_balance: bool
    uploaded_by: uuid.UUID
    uploaded_by_name: str | None
    uploaded_at: datetime
    reviewed_by: uuid.UUID | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VendorCreditListResponse(BaseModel):
    items: list[VendorCreditResponse]
    total: int


class CreditSuggestion(BaseModel):
    credit_id: uuid.UUID
    credit_number: str
    # The vendor's own credit-note number (e.g. "11DJ-MFHX-N4JG") — internal
    # UI shows both this and credit_number, since AP needs credit_number to
    # find the record here and vendor_credit_number to talk to the vendor.
    vendor_credit_number: str
    credit_date: date
    remaining: Decimal
    apply: Decimal


class CreditSuggestResponse(BaseModel):
    gross: Decimal
    suggested: list[CreditSuggestion]
    credit_applied: Decimal
    net: Decimal


class VendorCreditApplicationRow(BaseModel):
    """One (credit, payment) application — the audit trail for why a payment
    was short. `applied_by_name` is resolved from the users mirror at read
    time; the table stores only the id."""
    id: uuid.UUID
    credit_id: uuid.UUID
    payment_record_id: uuid.UUID
    batch_id: uuid.UUID | None
    doc_kind: str
    doc_id: uuid.UUID
    doc_number: str | None
    applied_amount: Decimal
    applied_at: datetime
    applied_by: uuid.UUID
    applied_by_name: str | None = None

    model_config = {"from_attributes": True}


class VendorCreditApplicationListResponse(BaseModel):
    items: list[VendorCreditApplicationRow]
    total: int
    total_applied: Decimal
