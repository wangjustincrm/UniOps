"""Schemas for the plant area tree."""
import uuid

from pydantic import BaseModel, ConfigDict, Field

# The levels actually present in the tree. `site` and `building` were in the
# original seed and were reshaped away by a later migration; accepting them
# would let somebody add a node at a level nothing else in the tree uses.
LEVELS = "^(area|department|line|sub_line|room|workstation)$"


class LocationCreate(BaseModel):
    # An unknown key is a mistake, not something to drop quietly.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    level: str = Field(pattern=LEVELS)
    # Optional: derived from the parent and the name when absent, because HSE
    # names areas and should not have to invent identifiers.
    code: str | None = Field(default=None, max_length=50)
    access_area: str | None = Field(default=None, max_length=30)
    department_id: uuid.UUID | None = None
    is_selectable: bool = True
    sort_order: int = 0


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    level: str | None = Field(default=None, pattern=LEVELS)
    access_area: str | None = Field(default=None, max_length=30)
    department_id: uuid.UUID | None = None
    is_selectable: bool | None = None
    sort_order: int | None = None


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    parent_id: uuid.UUID | None
    level: str
    path: str
    depth: int
    access_area: str | None
    department_id: uuid.UUID | None
    qr_token: str | None
    is_active: bool
    is_selectable: bool
    sort_order: int
