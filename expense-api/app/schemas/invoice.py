"""Pydantic schemas for expense invoices."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class InvoiceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_amount: Decimal
    unit: Optional[str] = None
    budget_account_id: Optional[uuid.UUID]
    budget_account_code: Optional[str]
    budget_account_name: Optional[str]


class InvoiceLineUpdate(BaseModel):
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    unit: Optional[str] = None
    budget_account_id: Optional[uuid.UUID] = None
    budget_account_code: Optional[str] = None
    budget_account_name: Optional[str] = None


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    file_mime_type: str
    file_size_bytes: int

    invoice_number: Optional[str]
    vendor_id: Optional[uuid.UUID]
    vendor_name: Optional[str]
    invoice_date: Optional[date]
    due_date: Optional[date]
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal

    ocr_confidence: Optional[Decimal]
    low_confidence_fields: list[str]
    status: str

    confirmed_by: Optional[uuid.UUID]
    confirmed_at: Optional[datetime]
    pa_id: Optional[uuid.UUID]
    pa_number: Optional[str]

    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    lines: list[InvoiceLineResponse] = []


class InvoiceUpdate(BaseModel):
    """Manual correction of OCR-extracted fields."""
    invoice_number: Optional[str] = None
    vendor_id: Optional[uuid.UUID] = None
    vendor_name: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: Optional[str] = None
    subtotal: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    confirmed: Optional[bool] = None  # True → set status to reviewed
    lines: Optional[list[InvoiceLineUpdate]] = None


class VendorSuggestion(BaseModel):
    id: uuid.UUID
    name: str
    code: str
