"""Request and response shapes for incidents."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from app.services import incident_type
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

FORM_KINDS = ("medical", "equipment", "near_miss")
INJURY_CLASSES = ("first_aid", "medical_aid", "lost_time")
MOL_REASONS = ("critical_injury", "fatality")


class IncidentPersonIn(BaseModel):
    role: str = Field(pattern="^(injured|involved|witness|first_aider)$")
    user_id: uuid.UUID | None = None
    # Populated for everyone: a name snapshot for employees, and the only
    # identifier available for contractors and visitors.
    person_name: str = Field(min_length=1, max_length=255)
    external_company: str | None = Field(default=None, max_length=200)
    # Carried from the staff picker (E55) rather than asked for again.
    department_id: uuid.UUID | None = None
    body_part_id: uuid.UUID | None = None
    body_part_label: str | None = Field(default=None, max_length=200)
    nature_of_injury_id: uuid.UUID | None = None
    nature_of_injury_label: str | None = Field(default=None, max_length=200)
    # What the first aid register held. On the person, because one incident can
    # hurt two people differently.
    treatment: str | None = None
    sent_offsite: bool | None = None
    follow_up: str | None = None


class IncidentCreate(BaseModel):
    form_kind: str = Field(pattern="^(medical|equipment|near_miss)$")
    title: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    location_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    shift_code: str | None = Field(default=None, max_length=20)
    category_id: uuid.UUID | None = None
    description: str | None = None
    equipment_involved: str | None = None
    witnesses_note: str | None = None
    immediate_action_taken: str | None = None
    reported_to_id: uuid.UUID | None = None
    is_anonymous: bool = False
    # One of the twelve options on HSE's form. The server fans it out into
    # injury_class / mol_reportable / category — see services/incident_type.py.
    # Explicit injury_class still wins, which is how Fatality and Unconscious
    # carry a severity the type itself does not imply.
    incident_type: str | None = None
    injury_class: str | None = None
    # Base64 PNG, drawn on the pad or taken from the reporter's profile. The
    # form has always captured one; until now there was nowhere to put it, so
    # every signature drawn since launch was discarded on submit.
    signature: str | None = None
    persons: list[IncidentPersonIn] = Field(default_factory=list)
    # Form-specific sections that have no column of their own.
    extra: dict = Field(default_factory=dict)

    @field_validator("incident_type")
    @classmethod
    def _known_incident_type(cls, v: str | None) -> str | None:
        # Validated here rather than in crud so an unknown type is a 422 the
        # caller can act on, not a 500 that reads like the server broke.
        if v is not None and v not in incident_type.BY_CODE:
            allowed = ", ".join(sorted(incident_type.BY_CODE))
            raise ValueError(f"Unknown incident type '{v}'. One of: {allowed}")
        return v

    @model_validator(mode="after")
    def _occurred_at_must_be_aware(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError(
                "occurred_at must include a timezone offset — the statutory "
                "clocks depend on which local day it falls on"
            )
        return self


class IncidentClassify(BaseModel):
    """Two independent facts, deliberately not one list of five.

    An incident can be both a lost-time injury and reportable to the Ministry;
    they start different clocks from different instants.
    """

    injury_class: str | None = Field(default=None, pattern="^(first_aid|medical_aid|lost_time)$")
    mol_reportable: bool = False
    mol_reportable_reason: str | None = Field(default=None, pattern="^(critical_injury|fatality)$")
    # When the employer learned of the reporting obligation. Starts the WSIB
    # clock, which is a different instant from the occurrence.
    employer_aware_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.mol_reportable and not self.mol_reportable_reason:
            raise ValueError(
                "mol_reportable_reason is required when an incident is reportable "
                "to the Ministry (critical_injury or fatality)"
            )
        if self.mol_reportable_reason and not self.mol_reportable:
            raise ValueError("mol_reportable_reason set but mol_reportable is false")
        if self.employer_aware_at and self.employer_aware_at.tzinfo is None:
            raise ValueError("employer_aware_at must include a timezone offset")
        return self


class DeadlineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    regulation_ref: str | None
    clock_type: str
    starts_at: datetime
    due_at: datetime
    satisfied_at: datetime | None
    escalation_level: int


class MolReportFiledIn(BaseModel):
    """Recording that the written report went to the Ministry.

    OHSA s.51(1) is discharged by sending the report, not by anything UniOps
    does, so this records the act rather than performing it. A reference is
    required for the same reason WSIB's is: a legal clock marked satisfied
    with nothing behind it is worse than one still showing as open — at an
    inspection the second is a gap, the first is an unsupported claim.
    """

    reference: str = Field(
        min_length=1, max_length=200,
        description="Where it was sent and any acknowledgement — the evidence it left.")
    filed_at: datetime | None = None
    evidence_file_id: uuid.UUID | None = None


class IncidentPersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    user_id: uuid.UUID | None
    person_name: str
    external_company: str | None
    # Ids alongside the labels. The label is the snapshot that keeps a record
    # readable after somebody renames the vocabulary entry; the id is what the
    # figures group on and what puts a picker back on the right row when the
    # record is reopened. This is the third place in the module that returned
    # the label alone — see the same note on hazards and safety equipment.
    body_part_id: uuid.UUID | None
    body_part_label: str | None
    nature_of_injury_id: uuid.UUID | None
    nature_of_injury_label: str | None
    treatment: str | None
    sent_offsite: bool | None
    follow_up: str | None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_no: str
    form_kind: str
    title: str
    status: str
    occurred_at: datetime | None
    employer_aware_at: datetime | None
    location_id: uuid.UUID | None
    location_path: str | None
    department_id: uuid.UUID | None
    shift_code: str | None
    category_label: str | None
    injury_class: str | None
    mol_reportable: bool
    mol_reportable_reason: str | None
    reported_by: uuid.UUID | None
    reported_by_name: str | None
    is_anonymous: bool
    description: str | None
    equipment_involved: str | None
    witnesses_note: str | None
    immediate_action_taken: str | None
    reported_to_name: str | None
    signature: str | None
    cost: Decimal | None
    lost_time_hours: Decimal | None
    hse_reviewed_at: datetime | None
    hse_reviewed_by_name: str | None
    signer_name: str | None
    signer_role: str | None
    signed_at: datetime | None
    # Answers to the sections that belong to one form kind only — the near-miss
    # "did you talk to the operator" pair lives here.
    extra: dict = Field(default_factory=dict)
    classified_at: datetime | None
    classified_by_name: str | None
    submitted_at: datetime | None
    closed_at: datetime | None
    reopened_at: datetime | None = None
    reopened_by_name: str | None = None
    created_at: datetime
    persons: list[IncidentPersonOut] = Field(default_factory=list)
    deadlines: list[DeadlineOut] = Field(default_factory=list)


class IncidentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_no: str
    form_kind: str
    title: str
    status: str
    occurred_at: datetime | None
    location_path: str | None
    injury_class: str | None
    mol_reportable: bool
