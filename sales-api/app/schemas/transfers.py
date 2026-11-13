"""Transfer DTOs (FR-OB-80 … 84)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.orders import TimelineEntry
from app.schemas.records import RecordDocumentRead

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class TransferLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: Code
    qty_ea: Annotated[int, Field(gt=0)]


class TransferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_warehouse_id: int
    to_warehouse_id: int
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    lines: Annotated[list[TransferLineIn], Field(min_length=1, max_length=200)]


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason


class TransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transfer_number: str
    from_warehouse_id: int
    to_warehouse_id: int
    status: str
    notes: str | None
    document_id: int | None
    error: str | None
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    from_warehouse: str | None = None   # code (filled by the service)
    to_warehouse: str | None = None
    # The list's facts (user 2026-11-13: as the orders' list): read with the row.
    sent_ea: int = 0
    received_ea: int = 0
    items: str | None = None
    receipt_date: date | None = None
    attachments: int = 0


class TransferLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_no: int
    item_code: str
    qty_ea: int
    received_ea: int
    line_status: str


class ReceiptRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    position: int
    lx_number: str | None
    item_code: str | None
    upc: str | None
    lot: str | None
    description: str | None
    qty_ea: int
    transfer_line_no: int | None


class ReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    document_id: int
    receipt_date: date | None
    total_ea: int | None
    applied: bool
    shadow: bool
    outcome: str | None
    discrepancies: list[str] | None
    created_at: datetime
    document_name: str | None = None
    rows: list[ReceiptRowRead] = []


class TransferDetail(TransferRead):
    lines: list[TransferLineRead] = []
    receipts: list[ReceiptRead] = []
    ack_status: str | None = None     # the live 943's 997 status
    file_status: str | None = None
    control: str | None = None
    status_before_hold: str | None = None
    documents: list[RecordDocumentRead] = []   # received, sent and attached (record_files)
    timeline: list[TimelineEntry] = []


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason
