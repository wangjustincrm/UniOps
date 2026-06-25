"""Cross-service ledger write schemas (commit / release / actualize / book-expense)."""
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class LedgerEntry(BaseModel):
    cost_center_id: uuid.UUID
    account_id: uuid.UUID
    fiscal_year: int = Field(ge=2020, le=2100)
    month: int = Field(ge=1, le=12)
    amount: Decimal


class CommitRequest(LedgerEntry):
    """commit / release / actualize all use this shape."""
    source_doc_type: str = Field(min_length=1, max_length=30)
    source_doc_id: uuid.UUID
    notes: str | None = None


class BookExpenseLine(LedgerEntry):
    pass


class BookExpenseRequest(BaseModel):
    source_doc_type: str = Field(min_length=1, max_length=30)
    source_doc_id: uuid.UUID
    lines: list[BookExpenseLine] = Field(min_length=1)
    notes: str | None = None


class LedgerWriteResponse(BaseModel):
    idempotent: bool
    inserted: int
    ledger_ids: list[uuid.UUID] = []
