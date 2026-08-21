"""Pydantic schemas for Goods Receipt (GR)."""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

PHYSICAL_TYPES = {1, 2, 3, 5}   # Raw Materials, Consumables, Spare Parts, Fixed Assets
# Service (4) and Project-Related (6) follow the service GR flow: the requester
# confirms completion instead of the warehouse receiving goods. Everything that
# branches on "is this a service?" must read this set — before it existed the
# pair was spelled out inline in api/v1/gr.py while the PR form only asked type
# 4 for a completion date, so type 6 silently had no date to work from.
SERVICE_TYPES = {4, 6}
GR_STATUSES = {"pending_ack", "collection_pending", "collected", "confirmed", "discrepancy", "rejected", "cancelled"}
# A GR in one of these statuses does not discharge the obligation to receive —
# the requester still has to produce a good one, so the due-date sweep keeps
# nudging.
GR_VOID_STATUSES = {"rejected", "cancelled"}


def is_physical(procurement_type: int) -> bool:
    return procurement_type in PHYSICAL_TYPES


def is_service(procurement_type: int) -> bool:
    return procurement_type in SERVICE_TYPES


# ── Line items ─────────────────────────────────────────────────────────────────

class GrLineItemIn(BaseModel):
    po_line_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    material_id: str | None = Field(default=None, max_length=50)
    qty_ordered: Decimal = Field(gt=0)
    qty_received: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, max_length=30)
    # unit_price may be 0 or negative, mirroring PO lines: a discount / rebate /
    # credit line carries a negative price so its line_total nets down the total.
    unit_price: Decimal
    condition: str = Field(default="good", max_length=20)
    discrepancy_notes: str | None = None
    actual_qty: Decimal | None = Field(default=None, ge=0)

    @property
    def line_total(self) -> Decimal:
        return (self.qty_received * self.unit_price).quantize(Decimal("0.01"))


class GrLineItemResponse(BaseModel):
    id: uuid.UUID
    po_line_id: uuid.UUID | None
    description: str
    material_id: str | None
    qty_ordered: Decimal
    qty_received: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal
    condition: str
    discrepancy_notes: str | None
    actual_qty: Decimal | None
    sort_order: int

    model_config = {"from_attributes": True}


# ── GR ────────────────────────────────────────────────────────────────────────

class GrAttachmentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    data: str  # base64-encoded file content


class GrCreate(BaseModel):
    po_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    currency: str = Field(default="CAD", max_length=10)
    storage_location: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    received_by: str | None = Field(default=None, max_length=200)
    line_items: list[GrLineItemIn] = Field(default_factory=list)
    attachments: list[GrAttachmentIn] = Field(default_factory=list)


class GrActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)
    # acknowledge: acknowledged_by name
    # collect: collected_by name, collection_notes
    acknowledged_by: str | None = None
    collected_by: str | None = None
    collection_notes: str | None = None
    comment: str | None = None
    # For line-level discrepancy updates on confirm
    line_conditions: list[dict] | None = None


class GrResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    po_id: uuid.UUID
    po_number: str
    pr_id: uuid.UUID | None
    pr_number: str | None
    vendor_id: uuid.UUID
    vendor_name: str
    gr_type: str
    procurement_type: int
    currency: str
    status: str
    storage_location: str | None
    received_by: str | None
    received_at: datetime | None
    notes: str | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    collected_at: datetime | None
    collected_by: str | None
    collection_notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    line_items: list[GrLineItemResponse]
    # NC ERP provenance — 'nc' for GRs mirrored from NC arrivals, None for GRs
    # created natively in UniOps. Read-only context surfaced on detail pages.
    source: str | None = None

    model_config = {"from_attributes": True}


class GrListResponse(BaseModel):
    items: list[GrResponse]
    total: int
