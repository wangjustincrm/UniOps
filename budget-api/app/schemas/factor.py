"""Pydantic schemas for decomposition factors and their values."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FactorValueBase(BaseModel):
    value_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    value_name: str = Field(min_length=1, max_length=255)
    sort_order: int = 0


class FactorValueCreate(FactorValueBase):
    pass


class FactorValueUpdate(BaseModel):
    value_name: str | None = Field(default=None, min_length=1, max_length=255)
    sort_order: int | None = None
    is_active: bool | None = None


class FactorValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    factor_id: uuid.UUID
    value_code: str
    value_name: str
    sort_order: int
    is_active: bool
    created_at: datetime


class FactorBase(BaseModel):
    factor_code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    factor_name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0


class FactorCreate(FactorBase):
    values: list[FactorValueCreate] = Field(default_factory=list)


class FactorUpdate(BaseModel):
    factor_name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None
    is_active: bool | None = None


class FactorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    factor_code: str
    factor_name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    values: list[FactorValueResponse] = []


# ── Factor Library (reusable templates) ───────────────────────────────────────

class FactorTemplateValueCreate(BaseModel):
    value_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    value_name: str = Field(min_length=1, max_length=255)
    sort_order: int = 0


class FactorTemplateValueUpdate(BaseModel):
    value_name: str | None = Field(default=None, min_length=1, max_length=255)
    sort_order: int | None = None
    is_active: bool | None = None


class FactorTemplateValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    value_code: str
    value_name: str
    sort_order: int
    is_active: bool
    created_at: datetime


class FactorTemplateCreate(BaseModel):
    factor_code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    factor_name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    values: list[FactorTemplateValueCreate] = Field(default_factory=list)


class FactorTemplateUpdate(BaseModel):
    # factor_code is immutable after creation (acts as the default copy key).
    factor_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class FactorTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    factor_code: str
    factor_name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    values: list[FactorTemplateValueResponse] = []


class FactorFromTemplate(BaseModel):
    """Create a per-Account factor by cloning a library template.

    factor_code/factor_name can be optionally overridden at copy time so two
    different accounts can both pull from the same template (e.g. 'channel')
    without colliding inside one account.
    """
    template_id: uuid.UUID
    factor_code: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    factor_name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int = 0
