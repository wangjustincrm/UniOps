"""Pydantic schemas for the shared catalog (L1 / Account)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── L1 ────────────────────────────────────────────────────────────────────────

class BudgetL1Base(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    sort_order: int = 0


class BudgetL1Create(BudgetL1Base):
    pass


class BudgetL1Update(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class BudgetL1Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class BudgetL1WithAccountsResponse(BudgetL1Response):
    accounts: list["BudgetAccountResponse"] = []


# ── Account ───────────────────────────────────────────────────────────────────

class BudgetAccountBase(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    sort_order: int = 0
    decomposition_enabled: bool = False


class BudgetAccountCreate(BudgetAccountBase):
    l1_id: uuid.UUID


class BudgetAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    decomposition_enabled: bool | None = None


class BudgetAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    l1_id: uuid.UUID
    is_active: bool
    decomposition_enabled: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


BudgetL1WithAccountsResponse.model_rebuild()


# ── CSV import / export ───────────────────────────────────────────────────────

class CatalogImportResult(BaseModel):
    l1_created: int
    l1_updated: int
    accounts_created: int
    accounts_updated: int
    errors: list[str] = []
