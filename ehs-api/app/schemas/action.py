"""Request and response shapes for corrective and preventive actions."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

SOURCE_TYPES = ("incident", "inspection", "jhsc", "audit", "observation",
                "hazard", "drill", "permit", "manual")
HIERARCHY = ("elimination", "substitution", "engineering", "administrative", "ppe")


class ActionCreate(BaseModel):
    source_type: str = Field(
        pattern="^(incident|inspection|jhsc|audit|observation|hazard|drill|permit|manual)$")
    source_id: uuid.UUID | None = None
    # The specific root cause this addresses, when it came from an
    # investigation. What lets the cause tree show whether anything is being
    # done about each cause.
    cause_id: uuid.UUID | None = None
    action_type: str = Field(default="corrective", pattern="^(corrective|preventive)$")
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    hierarchy_of_control: str | None = Field(
        default=None, pattern="^(elimination|substitution|engineering|administrative|ppe)$")
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")
    owner_id: uuid.UUID
    due_date: date
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _source_needs_an_id(self):
        if self.source_type != "manual" and self.source_id is None:
            raise ValueError(
                f"source_id is required when source_type is {self.source_type!r}; "
                "only a manually raised action has no source document"
            )
        return self


class ActionUpdateIn(BaseModel):
    body: str = Field(min_length=1)
    file_ids: list[uuid.UUID] = Field(default_factory=list)
    new_status: str | None = Field(
        default=None, pattern="^(open|in_progress|pending_verification)$")


class ActionVerifyIn(BaseModel):
    """Confirming the control works, which is not the same as confirming the
    work was done. A failed verification reopens the action."""

    is_effective: bool
    evidence: str | None = None
    file_ids: list[uuid.UUID] = Field(default_factory=list)


class ActionUpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_name: str | None
    body: str
    new_status: str | None
    created_at: datetime


class ActionVerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verified_by_name: str | None
    verified_at: datetime
    is_effective: bool
    evidence: str | None


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_no: str
    source_type: str
    source_id: uuid.UUID | None
    source_ref: str | None
    cause_id: uuid.UUID | None
    action_type: str
    title: str
    description: str | None
    hierarchy_of_control: str | None
    priority: str
    status: str
    owner_id: uuid.UUID | None
    owner_name: str | None
    due_date: date
    escalation_level: int
    verified_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class ActionDetail(ActionOut):
    updates: list[ActionUpdateOut] = Field(default_factory=list)
    verifications: list[ActionVerificationOut] = Field(default_factory=list)
