import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PostingLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_no: int
    line_role: str
    account_code: str | None
    cost_center_id: uuid.UUID | None
    partner_id: uuid.UUID | None
    partner_name: str | None
    debit: Decimal
    credit: Decimal
    tax_code: str | None
    currency: str
    fx_rate: Decimal
    memo: str | None


class PostingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_service: str
    source_doc_type: str
    source_doc_id: uuid.UUID
    source_doc_number: str
    event_type: str
    occurred_at: datetime
    status: str
    lines: list[PostingLineOut]
