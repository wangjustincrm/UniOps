"""Balance query schema — returned to epms-api for PR over-budget check."""
import uuid
from decimal import Decimal

from pydantic import BaseModel


class BalanceResponse(BaseModel):
    cost_center_id: uuid.UUID
    account_id: uuid.UUID
    account_code: str
    fiscal_year: int
    annual_budget: Decimal
    committed: Decimal
    actual_spent: Decimal
    available: Decimal
