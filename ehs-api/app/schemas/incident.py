"""Request and response shapes for incidents."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services import incident_type
from pydantic import (
    BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator,
)

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


# How far ahead of this server a reporting device may be. Clock skew between
# a phone and a server, not a margin for getting the date wrong.
FUTURE_TOLERANCE = timedelta(minutes=10)


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
    equipment_id: uuid.UUID | None = None
    witnesses_note: str | None = None
    immediate_action_taken: str | None = None
    batch_code: str | None = Field(default=None, max_length=100)
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
        # ★ HSE's answer in round 4: "refuse future dates on all 3 incident
        # types. warn the user that they selected a future date by mistake."
        #
        # It is not tidiness. MOL's forty-eight hours and WSIB's three business
        # days both run from the occurrence, so a date typed a week ahead sets
        # both clocks in the future — the incident then sits outside every
        # overdue report while the real deadline passes unnoticed. Refused where
        # the record is created rather than warned about on the screen, because
        # a warning that can be clicked past is how the clock gets set wrong.
        #
        # The tolerance absorbs clock skew between a phone and this server, not
        # a mistake: a few minutes is the difference between two devices, and
        # anything beyond it is somebody typing the wrong date.
        if self.occurred_at > datetime.now(timezone.utc) + FUTURE_TOLERANCE:
            raise ValueError(
                "This is dated in the future. An incident is reported after it "
                "happens, and the MOL and WSIB clocks both run from when it "
                "happened — check the date and time."
            )
        return self


class IncidentEdit(BaseModel):
    """What a supervisor may correct on a report after it is sent.

    Round 6, box 4: "Supervisors shall have edit rights to all incidents, near
    misses and hazard assessments to help manage them." The account of what
    happened — not the kind of incident or how badly somebody was hurt, which
    are the classification, start statutory clocks, and have their own step.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_at: datetime | None = None
    location_id: uuid.UUID | None = None
    shift_code: str | None = Field(default=None, max_length=20)
    description: str | None = None
    equipment_involved: str | None = None
    equipment_id: uuid.UUID | None = None
    witnesses_note: str | None = None
    immediate_action_taken: str | None = None
    batch_code: str | None = Field(default=None, max_length=100)


class IncidentClassify(BaseModel):
    """Two independent facts, deliberately not one list of five.

    An incident can be both a lost-time injury and reportable to the Ministry;
    they start different clocks from different instants.
    """

    # ★ "none" means the reporter was asked and said nobody was hurt. It is not
    # the same as not answering, and the difference decides whether the type's
    # own class is used as a fallback — round 5 added the picker to the three
    # types that imply their own severity, so "Nobody was hurt" on a First Aid
    # Injury has to be able to disagree with the type and win. Raised in review
    # by uniops-41, whose reading of the code was right and whose reading of my
    # commit message was that it claimed the opposite.
    injury_class: str | None = Field(
        default=None, pattern="^(first_aid|medical_aid|lost_time|none)$")
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
    # "Not required: <reason>" when HSE stopped a clock that did not apply.
    evidence_note: str | None = None


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


class IncidentEventOut(BaseModel):
    """A row of the timeline that can happen more than once (round 9)."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    at: datetime
    by_name: str | None
    detail: str | None


class ReopenIn(BaseModel):
    """Round 9: "when reopening an incident, require a mandatory reason"."""

    reason: str = Field(min_length=3, max_length=2000)


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
    # Set only for HSE, on an anonymous report: who filed it. Round 6, box 4.
    anonymous_reporter_name: str | None = None
    description: str | None
    equipment_involved: str | None
    batch_code: str | None = None
    # Round 12, Q8: whether the investigation must use the 5 Whys.
    needs_five_whys: bool = False
    equipment_id: uuid.UUID | None = None
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
    events: list[IncidentEventOut] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def incident_type_label(self) -> str | None:
        """Which of HSE's twelve options the reporter picked.

        ★ Round 4, on the submitted report: "the kind of incident … not
        displayed on the submitted form". The screen showed `form_kind` —
        medical, equipment, near miss — which is the form it came in on, not
        the choice the reporter made. The type itself was stored, read by the
        notifier, and shown back to nobody.

        Derived here rather than in the frontend for the same reason the
        mapping lives on the server: the list will change again, and a second
        copy of it drifts.
        """
        code = (self.extra or {}).get("incident_type")
        if not isinstance(code, str):
            return None
        # ★ As recorded, when it was (B3/B4 review L2); before that, as named now.
        kept = (self.extra or {}).get("incident_type_label")
        if isinstance(kept, str) and kept:
            return kept
        chosen = incident_type.BY_CODE.get(code)
        return chosen.label if chosen else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def incident_type_code(self) -> str | None:
        """The code behind the label, so the page can offer a change of it."""
        code = (self.extra or {}).get("incident_type")
        return code if isinstance(code, str) else None


class IncidentReclassify(BaseModel):
    """Round 7: the kind of incident, changed by the investigator before it closes."""

    incident_type: str
    # As on the report: a class, "none" for nobody hurt, or left out to take
    # what the new kind implies.
    injury_class: str | None = Field(
        default=None, pattern="^(first_aid|medical_aid|lost_time|none)$")


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
