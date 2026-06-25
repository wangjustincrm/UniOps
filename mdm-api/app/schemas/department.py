"""Pydantic schemas for Department (mdm-api)."""
import uuid

from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


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


class DepartmentListResponse(BaseModel):
    items: list[DepartmentResponse]
    total: int
