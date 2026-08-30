"""Training records, certifications, and the gap report."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_statutory: bool
    applies_to_all: bool
    validity_months: int | None
    is_active: bool


class TrainingRecordIn(BaseModel):
    user_id: uuid.UUID
    course_id: uuid.UUID
    completed_on: date
    # Left unset, it is derived from the course's validity period. Set it
    # explicitly when the certificate on the desk says something else.
    expires_on: date | None = None
    delivery: str = Field(default="internal", pattern="^(internal|external)$")
    certificate_file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _expiry_after_completion(self):
        if self.expires_on and self.expires_on <= self.completed_on:
            raise ValueError("expires_on must be after completed_on")
        return self


class TrainingRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    course_id: uuid.UUID | None
    course_label: str
    completed_on: date
    expires_on: date | None
    delivery: str
    certificate_file_id: uuid.UUID | None
    recorded_by_name: str | None
    created_at: datetime


class CertificationIn(BaseModel):
    user_id: uuid.UUID
    cert_type_id: uuid.UUID | None = None
    cert_type_label: str = Field(min_length=1, max_length=200)
    cert_no: str | None = Field(default=None, max_length=80)
    issuer: str | None = Field(default=None, max_length=200)
    issued_on: date | None = None
    expires_on: date | None = None
    file_id: uuid.UUID | None = None
    # When set, an expired certification blocks assignment to work that needs
    # it — an expired lift-truck licence stops being a reporting line and
    # starts being a gate.
    is_blocking: bool = False


class CertificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    cert_type_label: str
    cert_no: str | None
    issuer: str | None
    issued_on: date | None
    expires_on: date | None
    file_id: uuid.UUID | None
    is_blocking: bool
    # Computed for the caller so a list can be rendered without every client
    # re-implementing the comparison.
    is_expired: bool = False
    days_until_expiry: int | None = None


class GapRow(BaseModel):
    """One person missing one course."""

    user_id: uuid.UUID
    user_name: str
    course_id: uuid.UUID
    course_code: str
    course_name: str
    # missing = never taken. expired = taken, no longer valid.
    reason: str
    expired_on: date | None = None


class GapReport(BaseModel):
    as_of: date
    workers_considered: int
    workers_compliant: int
    compliance_rate: float
    gaps: list[GapRow] = Field(default_factory=list)
