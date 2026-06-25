"""Pydantic schemas for Department and CostCenter."""
import uuid

from pydantic import BaseModel, Field


# ── Department ─────────────────────────────────────────────────────────────────

class DepartmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class DepartmentListResponse(BaseModel):
    items: list["DepartmentResponse"]
    total: int


class CostCenterBrief(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class DepartmentResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    cost_centers: list[CostCenterBrief] = []

    model_config = {"from_attributes": True}


# ── CostCenter ─────────────────────────────────────────────────────────────────

class CostCenterCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    department_id: uuid.UUID


class CostCenterUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None


class CostCenterResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    department_id: uuid.UUID

    model_config = {"from_attributes": True}
