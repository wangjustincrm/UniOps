"""Schemas for PO Fee Invoices (crud.po_fee)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FeeLinkItem(BaseModel):
    fee_type: str = Field(pattern="^(freight|customs|other)$")
    po_id: uuid.UUID
    # Omitted only for an EPMS-raised PO with no arrival yet (crud.po_fee refuses otherwise).
    gr_id: uuid.UUID | None = None
    # invoices.line_items[].id; omitted = the whole invoice.
    invoice_line_id: uuid.UUID | None = None
    # Required when fee_type is "other": what the fee is. Ignored otherwise.
    note: str | None = Field(default=None, max_length=200)


class FeeLinksPut(BaseModel):
    """The invoice's COMPLETE link set — an empty list removes them all."""
    links: list[FeeLinkItem] = Field(default_factory=list, max_length=200)


class FeeLinkOut(BaseModel):
    id: uuid.UUID
    invoice_line_id: uuid.UUID | None
    fee_type: str
    note: str | None = None
    po_id: uuid.UUID
    po_number: str
    po_vendor_name: str
    gr_id: uuid.UUID | None
    gr_number: str | None
    gr_received_at: datetime | None
