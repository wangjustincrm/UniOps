"""Pydantic schemas for Parts Catalog."""
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class PartCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    category: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    supplier: str = Field(min_length=1, max_length=255)
    supplier_part_no: str = Field(min_length=1, max_length=100)
    supplier_item_id: str | None = Field(default=None, max_length=100)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    unit: str = Field(min_length=1, max_length=50)
    image_data_url: str | None = None


class PartUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    supplier: str | None = Field(default=None, min_length=1, max_length=255)
    supplier_part_no: str | None = Field(default=None, min_length=1, max_length=100)
    supplier_item_id: str | None = Field(default=None, max_length=100)
    unit_price: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    image_data_url: str | None = None
    is_active: bool | None = None


class PartResponse(BaseModel):
    id: uuid.UUID
    code: str
    category: str
    name: str
    description: str | None
    supplier: str
    supplier_part_no: str
    supplier_item_id: str | None
    unit_price: Decimal
    unit: str
    image_data_url: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class PartCsvRow(BaseModel):
    """One row from a CSV import."""
    code: str
    category: str
    name: str
    description: str | None = None
    supplier: str
    supplier_part_no: str
    supplier_item_id: str | None = None
    unit_price: Decimal = Decimal("0")
    unit: str


class PartListResponse(BaseModel):
    items: list[PartResponse]
    total: int


class PartImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]
