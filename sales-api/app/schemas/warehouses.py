"""Warehouse DTOs (design §3.1; FR-OB-27): the 943's ship-from / ship-to."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.schemas.common import not_excel_number

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50), AfterValidator(not_excel_number)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
Role = Literal["SHIPPER", "WAREHOUSE", "TRANSFER_SOURCE"]


class WarehouseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Code
    name: Name
    role: Role
    edi_id_qualifier: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)] | None = None
    edi_id: Short | None = None
    address1: Text | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    province: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10)] | None = None
    zip: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    country: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)] | None = None
    address_lines: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    active: bool = True


class WarehouseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name = None  # type: ignore[assignment]
    role: Role = None  # type: ignore[assignment]
    edi_id_qualifier: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)] | None = None
    edi_id: Short | None = None
    address1: Text | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    province: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10)] | None = None
    zip: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    country: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)] | None = None
    address_lines: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    active: bool = None  # type: ignore[assignment]


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    role: str
    edi_id_qualifier: str | None
    edi_id: str | None
    address1: str | None
    city: str | None
    province: str | None
    zip: str | None
    country: str | None
    address_lines: str | None
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None
