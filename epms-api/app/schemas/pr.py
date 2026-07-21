"""Pydantic schemas for Purchase Request (PR)."""
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator


# Mirrors budget-api factor_code/value_code patterns
_FACTOR_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VALUE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_factor_combo(value: dict | None) -> dict | None:
    """Shape check: {factor_code: value_code} with the same character set
    budget-api uses. Strict validation of whether the codes match the Account's
    actual factors lives in the Create PR UI (which reads the Account's factor
    list) — server-side does not call budget-api on every PR write for fail-open
    consistency with the rest of epms-api ↔ budget-api integration.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("factor_combo must be a JSON object")
    if not value:
        # Empty dict is rejected — clients should send None instead.
        raise ValueError("factor_combo must contain at least one factor when provided")
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("factor_combo keys and values must be strings")
        if not _FACTOR_CODE_RE.match(k):
            raise ValueError(f"factor_combo key '{k}' is not a valid factor_code")
        if not v.strip():
            raise ValueError(f"factor_combo value for '{k}' is empty")
        if not _VALUE_CODE_RE.match(v):
            raise ValueError(f"factor_combo value '{v}' for '{k}' is not a valid value_code")
    return value

PR_STATUSES = {
    "draft", "submitted", "in_review", "approved",
    "returned", "rejected", "cancelled", "issued",
}

PR_WORKFLOW = [
    {"step": 0, "role": "dept_manager",    "label": "Department Manager"},
    {"step": 1, "role": "gm",              "label": "GM / OPM Approval"},
    {"step": 2, "role": "finance_manager", "label": "Finance Manager"},
]


# ── Line items ─────────────────────────────────────────────────────────────────

class PrLineItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    material_id: str | None = Field(default=None, max_length=50)
    supplier_item_id: str | None = Field(default=None, max_length=100)
    qty: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)
    unit_price: Decimal = Field(ge=0)
    notes: str | None = None

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))


class PrLineItemResponse(BaseModel):
    id: uuid.UUID
    description: str
    material_id: str | None
    supplier_item_id: str | None
    qty: Decimal
    unit: str
    unit_price: Decimal
    line_total: Decimal
    notes: str | None
    sort_order: int

    model_config = {"from_attributes": True}


# ── PR ────────────────────────────────────────────────────────────────────────

class PrCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    type: int = Field(ge=1, le=6)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    vendor_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    factor_combo: dict[str, str] | None = None
    project_code: str | None = Field(default=None, max_length=100)
    required_by: date | None = None
    delivery_address: str | None = None
    notes: str | None = None
    is_prepaid: bool = False
    over_budget_justification: str | None = None
    line_items: list[PrLineItemIn] = Field(default_factory=list)

    @field_validator("factor_combo")
    @classmethod
    def _check_factor_combo(cls, v: dict | None) -> dict | None:
        return _validate_factor_combo(v)


class PrUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    type: int | None = Field(default=None, ge=1, le=6)
    currency: str | None = Field(default=None, max_length=10)
    vendor_id: uuid.UUID | None = None
    cost_center_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    factor_combo: dict[str, str] | None = None
    project_code: str | None = Field(default=None, max_length=100)
    required_by: date | None = None
    delivery_address: str | None = None
    notes: str | None = None
    is_prepaid: bool | None = None
    over_budget_justification: str | None = None
    line_items: list[PrLineItemIn] | None = None  # None = don't touch; [] = clear all

    @field_validator("factor_combo")
    @classmethod
    def _check_factor_combo(cls, v: dict | None) -> dict | None:
        return _validate_factor_combo(v)


class BudgetCheckRequest(BaseModel):
    """Inputs mirroring the PR create form used to determine over-budget.

    Only budget_code + cost_center_id + amount drive the computation (same as
    the create path); department_id / factor_combo / project_code are accepted
    for form parity but do not affect the result.
    """
    cost_center_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    factor_combo: dict[str, str] | None = None
    project_code: str | None = Field(default=None, max_length=100)
    amount: Decimal = Field(default=Decimal("0"), ge=0)


class BudgetCheckResponse(BaseModel):
    over_budget: bool
    available: str | None  # decimal-as-str; None when balance can't be determined


class PrActionRequest(BaseModel):
    """Payload for submit / approve / return / reject / cancel."""
    action: str = Field(min_length=1, max_length=20)
    comment: str | None = None


class PrResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    type: int
    status: str
    currency: str
    amount: Decimal
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    is_prepaid: bool
    cost_center_id: uuid.UUID | None
    cost_center_name: str | None
    department_name: str | None
    budget_code: str | None
    factor_combo: dict[str, str] | None = None
    project_code: str | None
    required_by: date | None
    delivery_address: str | None
    notes: str | None
    over_budget: bool
    over_budget_justification: str | None
    submitted_at: datetime | None
    approval_step_idx: int
    po_id: uuid.UUID | None
    po_number: str | None
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    line_items: list[PrLineItemResponse]

    model_config = {"from_attributes": True}


class PrListResponse(BaseModel):
    items: list[PrResponse]
    total: int


# ── Approval event ─────────────────────────────────────────────────────────────

class ApprovalEventResponse(BaseModel):
    id: uuid.UUID
    document_type: str
    document_id: uuid.UUID
    document_number: str
    step_idx: int
    action: str
    actor_id: uuid.UUID
    actor_role: str
    actor_name: str | None = None
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
