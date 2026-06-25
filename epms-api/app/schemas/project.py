"""Pydantic schemas for Project."""
import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

PROJECT_STATUSES = {"active", "on_hold", "closed"}


class ProjectCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: str = Field(default="active", max_length=20)
    budget: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    owner_dept: str | None = Field(default=None, max_length=50)
    manager_name: str | None = Field(default=None, max_length=255)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    budget: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=10)
    start_date: date | None = None
    end_date: date | None = None
    owner_dept: str | None = Field(default=None, max_length=50)
    manager_name: str | None = Field(default=None, max_length=255)


class ProjectResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    status: str
    budget: Decimal
    currency: str
    start_date: date | None
    end_date: date | None
    owner_dept: str | None
    manager_name: str | None

    model_config = {"from_attributes": True}
