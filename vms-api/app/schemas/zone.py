"""Schemas for access zones (VMS Admin → Zones)."""
import uuid

from pydantic import BaseModel, Field, field_validator


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    if not v:
        raise ValueError("Zone name is required")
    return v


class ZoneManagerOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    is_active: bool


class ZoneOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    gmp_grade: bool
    is_active: bool
    sort_order: int
    managers: list[ZoneManagerOut]
    # How many visits use the zone — a zone in use can be retired, not deleted.
    visit_count: int


class ZoneCreate(BaseModel):
    name: str = Field(max_length=80)
    gmp_grade: bool = False
    sort_order: int = 0
    # Empty = the zone needs no approval.
    manager_ids: list[uuid.UUID] = Field(default_factory=list)

    _name = field_validator("name")(_clean_name)


class ZoneUpdate(BaseModel):
    """Partial update. `manager_ids`, when sent, replaces the whole list."""
    name: str | None = Field(default=None, max_length=80)
    gmp_grade: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    manager_ids: list[uuid.UUID] | None = None

    _name = field_validator("name")(_clean_name)
