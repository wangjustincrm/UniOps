"""Pydantic schemas for CostCenter (mdm-api)."""
import uuid

from pydantic import BaseModel, Field


class CostCenterCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    department_id: uuid.UUID
    is_active: bool = True


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
    department_code: str = ""
    department_name: str = ""

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_dept(cls, cc) -> "CostCenterResponse":
        return cls(
            id=cc.id,
            code=cc.code,
            name=cc.name,
            is_active=cc.is_active,
            department_id=cc.department_id,
            department_code=cc.department.code if cc.department else "",
            department_name=cc.department.name if cc.department else "",
        )
