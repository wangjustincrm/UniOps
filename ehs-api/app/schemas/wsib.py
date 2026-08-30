"""The WSIB Form 7 data package.

UniOps does not file Form 7. Statutory submission goes through WSIB's own
service and no third party can do it on an employer's behalf, so what this
produces is everything needed to fill that form in one sitting: the fields we
hold, and — just as importantly — a list of the ones we do not, so whoever
files it knows what to gather before they start rather than discovering it
half way through.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class MissingField(BaseModel):
    field: str
    why_it_matters: str


class Form7Package(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: uuid.UUID
    incident_no: str
    generated_at: datetime

    # ── Section 1: the employer ─────────────────────────────────────────────
    employer_name: str
    employer_address: str | None

    # ── Section 2: the worker ───────────────────────────────────────────────
    worker_name: str | None
    worker_employee_no: str | None
    worker_department: str | None
    worker_hire_date: date | None
    worker_is_employee: bool
    worker_external_company: str | None

    # ── Section 3: the injury ───────────────────────────────────────────────
    occurred_at: datetime | None
    employer_aware_at: datetime | None
    location_path: str | None
    body_part: str | None
    nature_of_injury: str | None
    injury_class: str | None
    description: str | None
    witnesses: str | None

    # ── Section 4: treatment and lost time ──────────────────────────────────
    treatment: str | None
    sent_offsite: bool | None
    modified_duties: str | None
    at_regular_pay: bool | None
    rtw_start_date: date | None

    # ── The clock ───────────────────────────────────────────────────────────
    due_at: datetime | None
    internal_target: datetime | None
    filed_at: datetime | None
    confirmation_number: str | None

    # ── What still has to be gathered ───────────────────────────────────────
    missing: list[MissingField] = Field(default_factory=list)
    is_complete: bool = False


class Form7FiledIn(BaseModel):
    """Recording that the form was filed, which is what stops the clock."""

    confirmation_number: str = Field(
        min_length=1, max_length=100,
        description="The reference WSIB returns on submission — the evidence that it arrived.")
    filed_at: datetime | None = None
    evidence_file_id: uuid.UUID | None = None
