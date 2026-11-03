"""Carrier DTOs (sales19): the 945's SCAC, its name and its tracking link template."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.domain.carriers import PLACEHOLDER

Scac = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=10)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


def _template(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not v.startswith("https://"):
        raise ValueError("an https:// address")
    if PLACEHOLDER not in v:
        raise ValueError(f"put {PLACEHOLDER} where the tracking number goes")
    return v


Template = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500), AfterValidator(_template)]


class CarrierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scac: Scac
    name: Name
    tracking_url: Template | None = None
    active: bool = True


class CarrierUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name = None  # type: ignore[assignment]
    tracking_url: Template | None = None
    active: bool = None  # type: ignore[assignment]


class CarrierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scac: str
    name: str
    tracking_url: str | None
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None
