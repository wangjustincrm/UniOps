"""Pydantic schemas for expense policy configuration."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ── CFM field definition ──────────────────────────────────────────────────────

class CfmFieldDef(BaseModel):
    """One field in a custom form definition."""
    name: str                           # snake_case identifier
    label: str                          # display label
    field_type: str                     # text | number | date | select | textarea
    required: bool = False
    options: list[str] = []             # for select fields
    placeholder: Optional[str] = None


class CustomFormDef(BaseModel):
    """A custom form type (CFM)."""
    code: str                           # e.g. "CONF" → action key "cfm_conf"
    name: str                           # e.g. "Conference & Training"
    fields: list[CfmFieldDef] = []
    is_active: bool = True


# ── Policy ────────────────────────────────────────────────────────────────────

class ExpensePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hst_rate: Decimal
    mileage_rate_per_km: Decimal
    mileage_budget_account_id: Optional[uuid.UUID]
    max_km_per_claim: int
    meal_breakfast_limit: Decimal
    meal_lunch_limit: Decimal
    meal_dinner_limit: Decimal
    meal_incidental_limit: Decimal
    custom_forms: list[Any] = []
    updated_at: datetime


class ExpensePolicyUpdate(BaseModel):
    hst_rate: Optional[Decimal] = None
    mileage_rate_per_km: Optional[Decimal] = None
    mileage_budget_account_id: Optional[uuid.UUID] = None
    max_km_per_claim: Optional[int] = None
    meal_breakfast_limit: Optional[Decimal] = None
    meal_lunch_limit: Optional[Decimal] = None
    meal_dinner_limit: Optional[Decimal] = None
    meal_incidental_limit: Optional[Decimal] = None
    custom_forms: Optional[list[Any]] = None
