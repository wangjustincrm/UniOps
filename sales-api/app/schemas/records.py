"""What an order and a transfer share (record_files): their files and attachments."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.orders import OrderDocumentRead


class RecordDocumentRead(OrderDocumentRead):
    """A file of the record: received, sent, or attached by a person (kind ATTACHMENT)."""
    size: int | None = None
    created_by_name: str | None = None
    note: str | None = None


class AttachmentRead(RecordDocumentRead):
    pass


class RemoveAttachmentIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
