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
    material_filename: str | None = None


class CourseIn(BaseModel):
    """A new course. The eight statutory ones are seeded; this is for the rest."""

    # ★ Round 8 (open box): "Training course code and name are the same, remove
    # the code and only use the course name." Optional now; left out, it is
    # made from the name (the record still needs a stable key).
    code: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    applies_to_all: bool = False
    validity_months: int | None = Field(default=None, ge=1, le=600)
    material_filename: str | None = None


class CoursePatch(BaseModel):
    """Changing a course.

    `applies_to_all` is the field this exists for. Four courses are required of
    everybody, which is HSE's own list — but a list nobody can change is a
    decision taken once and then enforced forever, and the compliance report
    counts every employee against it. Whether a course is statutory is not
    editable: that follows from the regulation, not from us.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    applies_to_all: bool | None = None
    validity_months: int | None = Field(default=None, ge=1, le=600)
    is_active: bool | None = None


class TrainingRecordIn(BaseModel):
    user_id: uuid.UUID
    course_id: uuid.UUID
    completed_on: date
    # Left unset, it is derived from the course's validity period. Set it
    # explicitly when the certificate on the desk says something else.
    expires_on: date | None = None
    delivery: str = Field(default="internal", pattern="^(internal|external)$")
    # Who gave it, for training taken elsewhere (round 9).
    provider: str | None = Field(default=None, max_length=200)
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
    provider: str | None = None
    certificate_file_id: uuid.UUID | None
    recorded_by_name: str | None
    created_at: datetime
    delivery_id: uuid.UUID | None = None
    # Read from the session this record came from, not stored here. Empty on a
    # record entered by hand, which had no session.
    run_by_name: str | None = None
    instructor_name: str | None = None
    # How long the session it came from ran. Round 6.
    length_minutes: int | None = None


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
    # Who a supervisor would go and talk to. Snapshot, like every other name in
    # this module — the report is read long after it was run.
    department_name: str | None = None


class CourseGapSummary(BaseModel):
    """One required course, and how far short of it the plant is.

    Counted here rather than derived in the browser because `required_of` is
    not visible from the gap rows: a course everybody has taken produces no
    rows at all, and would silently drop off a list built from them. It is also
    the number the Course sessions tab is scheduled against — "WHMIS, 87
    outstanding" is the size of the problem, not a filter result.
    """

    course_id: uuid.UUID
    course_code: str
    course_name: str
    required_of: int
    missing: int
    expired: int

    @property
    def outstanding(self) -> int:
        return self.missing + self.expired


class GapReport(BaseModel):
    as_of: date
    workers_considered: int
    workers_compliant: int
    compliance_rate: float
    gaps: list[GapRow] = Field(default_factory=list)
    courses: list[CourseGapSummary] = Field(default_factory=list)
