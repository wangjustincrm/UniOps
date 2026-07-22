"""Actual / committed monthly summary schemas."""
import uuid
from decimal import Decimal

from pydantic import BaseModel


class MonthlyActualRow(BaseModel):
    cost_center_id: uuid.UUID
    account_id: uuid.UUID
    account_code: str
    fiscal_year: int
    month: int
    committed: Decimal
    actual_spent: Decimal


class ActualsListResponse(BaseModel):
    items: list[MonthlyActualRow]
    total: int


class AccountSummary(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    l1_id: uuid.UUID
    l1_code: str
    annual_budget: Decimal
    committed: Decimal
    actual_spent: Decimal
    available: Decimal
    utilisation_pct: float


class ActualsSummaryResponse(BaseModel):
    cost_center_id: uuid.UUID | None
    fiscal_year: int
    accounts: list[AccountSummary]


# ── Monthly summary (Dashboard plan-vs-actual by month) ───────────────────────

class MonthlyAccountSummary(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    l1_id: uuid.UUID
    l1_code: str
    # 1..12 → planned amount (current approved plan) / actual spent (ledger)
    plan_by_month: dict[int, Decimal] = {}
    actual_by_month: dict[int, Decimal] = {}
    plan_year: Decimal
    actual_year: Decimal


class MonthlyActualsSummaryResponse(BaseModel):
    cost_center_id: uuid.UUID | None
    fiscal_year: int
    accounts: list[MonthlyAccountSummary]


# ── Scope (Budget Dashboard department scoping) ────────────────────────────────

class ScopeCostCenter(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    department_id: uuid.UUID | None


class ActualsScopeResponse(BaseModel):
    full_access: bool
    cost_centers: list[ScopeCostCenter]
