"""Pydantic schemas for budget plans, lines, and breakdowns."""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ── Breakdown (factor combination) ────────────────────────────────────────────

class BreakdownCreate(BaseModel):
    factor_combo: dict[str, str] = Field(..., description="e.g. {'brand':'BRAND_A','channel':'ONLINE'}")
    amount: Decimal = Field(default=Decimal("0"))
    notes: str | None = None


class BreakdownUpdate(BaseModel):
    factor_combo: dict[str, str] | None = None
    amount: Decimal | None = None
    notes: str | None = None


class BreakdownResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_line_id: uuid.UUID
    factor_combo: dict[str, str]
    amount: Decimal
    notes: str | None
    created_at: datetime


class BulkBreakdownReplaceItem(BaseModel):
    """One row in a bulk-replace payload (no id — backend re-inserts all rows)."""
    factor_combo: dict[str, str] = Field(..., description="Keys = factor_code, values = value_code")
    amount: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


class BulkBreakdownReplaceRequest(BaseModel):
    """Atomic replace of all breakdowns for a (plan_line) cell.

    Backend deletes all existing breakdowns for the line and inserts these.
    plan_line.amount is recomputed from the new breakdowns inside the same
    transaction (preserves BPLAN-004 invariant).
    """
    breakdowns: list[BulkBreakdownReplaceItem] = Field(default_factory=list)


class BulkBreakdownReplaceResponse(BaseModel):
    breakdowns: list[BreakdownResponse]
    line_amount: Decimal


class BaselineResponse(BaseModel):
    """Historical reference data shown in the matrix sidebar."""
    last_year_same_month: Decimal | None = None
    current_month_actual: Decimal | None = None
    ytd_actual: Decimal | None = None
    last_year_breakdowns: list[BreakdownResponse] = Field(default_factory=list)


# ── PlanLine ──────────────────────────────────────────────────────────────────

class PlanLineUpdate(BaseModel):
    amount: Decimal = Field(default=Decimal("0"))
    notes: str | None = None


class PlanLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    account_id: uuid.UUID
    month: int
    amount: Decimal
    notes: str | None
    breakdowns: list[BreakdownResponse] = []


# ── Plan ──────────────────────────────────────────────────────────────────────

class PlanCreate(BaseModel):
    cost_center_id: uuid.UUID
    fiscal_year: int = Field(ge=2020, le=2100)
    notes: str | None = None


class PlanUpdate(BaseModel):
    notes: str | None = None


class PlanActionRequest(BaseModel):
    action: str = Field(..., pattern=r"^(submit|approve|return|reject|cancel)$")
    comment: str | None = None


class PlanReviseRequest(BaseModel):
    """Create a new draft version revising an existing approved plan."""
    revision_notes: str | None = Field(
        default=None,
        description="Reason for the revision (e.g. 'Mid-year reallocation per CFO directive').",
    )


class PlanImportResult(BaseModel):
    """Outcome of a CSV plan import."""
    lines_updated: int = 0
    accounts_touched: int = 0
    accounts_skipped: int = 0
    # Count of (account, month) cells where an imported direct total replaced
    # existing factor breakdowns on a decomposition-enabled account.
    breakdowns_cleared: int = 0
    errors: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cost_center_id: uuid.UUID
    fiscal_year: int
    status: str
    submitted_at: datetime | None
    approved_at: datetime | None
    approval_step_idx: int
    notes: str | None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID

    # Revision lineage
    version: int
    parent_plan_id: uuid.UUID | None
    is_current: bool
    revision_notes: str | None


# ── Plan full grid (with lines + breakdowns + Q/Y aggregates) ─────────────────

class AccountPlanGrid(BaseModel):
    """Per-account row in the plan grid, with monthly + Q/Y aggregates."""
    account_id: uuid.UUID
    account_code: str
    account_name: str
    l1_id: uuid.UUID
    l1_code: str
    l1_name: str
    decomposition_enabled: bool
    months: dict[int, Decimal] = Field(default_factory=dict)   # 1..12 → amount
    breakdowns_by_month: dict[int, list[BreakdownResponse]] = Field(default_factory=dict)
    q1: Decimal = Decimal("0")
    q2: Decimal = Decimal("0")
    q3: Decimal = Decimal("0")
    q4: Decimal = Decimal("0")
    year_total: Decimal = Decimal("0")


class PlanGridResponse(BaseModel):
    plan: PlanResponse
    rows: list[AccountPlanGrid]
    grand_total: Decimal = Decimal("0")
