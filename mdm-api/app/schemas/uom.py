"""Pydantic schemas for Unit of Measure (mdm-api)."""
import uuid
from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal["count", "mass", "volume", "length", "area", "time", "other"]


class UomCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    dimension: Dimension = "other"
    is_active: bool = True


class UomUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    dimension: Dimension | None = None
    is_active: bool | None = None


class UomResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    dimension: str
    is_active: bool

    model_config = {"from_attributes": True}


class UomListResponse(BaseModel):
    items: list[UomResponse]
    total: int
