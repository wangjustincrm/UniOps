"""Pydantic schemas for the warehouse receiving report."""
import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ReceivingReportRow(BaseModel):
    gr_id: uuid.UUID
    gr_number: str
    po_id: uuid.UUID
    po_number: str
    material_id: str | None
    description: str
    supplier: str
    unit: str
    quantity: Decimal
    department: str | None
    requested_by: str | None
    # Plain dates, already resolved to the plant's local day by the query layer.
    # The front-end renders them as-is: parsing a date-only string through
    # `new Date()` in a UTC-4 browser lands on the previous day.
    date_ordered: date | None
    arrival_date: date | None
    left_warehouse_date: date | None
    warehouse_receiver: str | None
    person_accepting: str | None
    lead_time_days: int | None

    model_config = {"from_attributes": True}


class ReceivingReportResponse(BaseModel):
    items: list[ReceivingReportRow]
    total: int
    # True when the row cap cut the result short — the UI says so rather than
    # letting a half-answer look complete.
    truncated: bool
