"""Schemas for 期初 (opening balance) import of Budget Actuals."""
import uuid
from decimal import Decimal

from pydantic import BaseModel


class OpeningImportResult(BaseModel):
    """Outcome of an opening-balance CSV import."""
    rows_created: int = 0
    rows_updated: int = 0
    errors: list[str] = []


class OpeningBalanceRow(BaseModel):
    cost_center_id: uuid.UUID
    account_id: uuid.UUID
    account_code: str
    account_name: str
    fiscal_year: int
    month: int
    amount: Decimal


class OpeningListResponse(BaseModel):
    cost_center_id: uuid.UUID | None
    fiscal_year: int
    items: list[OpeningBalanceRow]
