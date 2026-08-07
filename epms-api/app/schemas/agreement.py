"""Request/response schemas for Purchase Agreement (AGR)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

# 独立审批流(用户决策:不复用 PO 链)。这是种子默认值 —— 生产以 CompanyConfig
# .workflow_defs["agr"] 为准,管理员可在 Portal Admin 改。
AGR_WORKFLOW = [
    {"step": 0, "role": "dept_manager",        "label": "Department Manager"},
    {"step": 1, "role": "procurement_manager", "label": "Procurement Manager"},
    {"step": 2, "role": "finance_manager",     "label": "Finance Manager"},
]

AGREEMENT_TYPES = ("house_account", "recurring", "milestone")


class AgreementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    agreement_type: str = Field(pattern="^(house_account|recurring|milestone)$")
    vendor_id: uuid.UUID
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date
    valid_to: date
    grace_days: int = Field(default=30, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validity_window_is_ordered(self):
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        return self


class AgreementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date | None = None
    valid_to: date | None = None
    grace_days: int | None = Field(default=None, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None


class AgreementActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)   # submit|approve|return|cancel
    comment: str | None = None


class AgreementResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    agreement_type: str
    contract_no: str | None
    contact_email: str | None
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_reference: str | None
    valid_from: date
    valid_to: date
    grace_days: int
    not_to_exceed: Decimal | None
    consumed_amount: Decimal
    currency: str
    tax_code: str | None
    tax_rate: Decimal | None
    department_id: uuid.UUID | None
    budget_code: str | None
    owner_id: uuid.UUID | None
    status: str
    approval_step_idx: int
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgreementListResponse(BaseModel):
    items: list[AgreementResponse]
    total: int
