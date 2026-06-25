"""Pydantic schemas for custom form definitions (CFM) — PRD-OA §5 / §12."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CfmFieldDef(BaseModel):
    """One field in a custom form definition (PRD §5.2)."""
    name: str
    label: str
    field_type: str = "text"   # text | textarea | number | date | select | budget_account | cost_centre | attachment | calculated
    required: bool = False
    options: list[str] = []
    placeholder: Optional[str] = None


class CustomFormResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: Optional[str] = None
    workflow_key: str = "cfm"
    default_currency: str = "CAD"
    # Exposed to the client as `fields`, persisted as `field_schema`.
    fields: list[CfmFieldDef] = Field(default_factory=list, validation_alias="field_schema")
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class CustomFormCreate(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    workflow_key: Optional[str] = None        # defaults to cfm_<code> if omitted
    default_currency: str = "CAD"
    fields: list[CfmFieldDef] = []
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _norm_code(cls, v: str) -> str:
        v = v.strip().upper().replace(" ", "_")
        if not v:
            raise ValueError("code is required")
        return v


class CustomFormUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workflow_key: Optional[str] = None
    default_currency: Optional[str] = None
    fields: Optional[list[CfmFieldDef]] = None
    is_active: Optional[bool] = None
