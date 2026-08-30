"""The first-aid register — Regulation 1101."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FirstAidIn(BaseModel):
    occurred_at: datetime
    injured_user_id: uuid.UUID | None = None
    # Always present: a name snapshot for employees, and the only identifier
    # there is for a contractor or visitor treated on site.
    injured_name: str = Field(min_length=1, max_length=255)
    first_aider_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    body_part_id: uuid.UUID | None = None
    body_part_label: str | None = Field(default=None, max_length=200)
    treatment_given: str = Field(min_length=1)
    sent_offsite: bool = False
    follow_up: str | None = None
    file_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _occurred_at_must_be_aware(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone offset")
        return self


class FirstAidOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    log_no: str
    incident_id: uuid.UUID | None
    occurred_at: datetime
    location_id: uuid.UUID | None
    location_path: str | None
    injured_user_id: uuid.UUID | None
    injured_name: str
    first_aider_name: str | None
    body_part_label: str | None
    treatment_given: str
    sent_offsite: bool
    follow_up: str | None
    created_at: datetime


class EscalateIn(BaseModel):
    """Turning a first-aid entry into a reportable incident.

    Treatment that looked like first aid at the time and turns out to need a
    doctor is the common path to a WSIB obligation, and the register entry is
    usually the only contemporaneous record of when it happened.
    """

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # When the employer learned this was more than first aid — the instant the
    # WSIB clock starts from, which is not when the treatment was given.
    employer_aware_at: datetime | None = None

    @model_validator(mode="after")
    def _aware_must_be_aware(self):
        if self.employer_aware_at and self.employer_aware_at.tzinfo is None:
            raise ValueError("employer_aware_at must include a timezone offset")
        return self
