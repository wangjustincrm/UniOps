"""Investigation, causes, and the cause tree the detail screen renders."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class InvestigationIn(BaseModel):
    # A PUT replaces the investigation, so anything left out is cleared. That is
    # the right contract for a form that submits whole, and a dangerous one
    # combined with silently ignoring fields it does not recognise: one
    # misspelled key and the whole investigation is blanked, unrecoverably,
    # with a 200 back. Rejecting the unknown key costs nothing and turns that
    # into a 422.
    model_config = ConfigDict(extra="forbid")

    investigator_id: uuid.UUID | None = None
    supporting_investigator_ids: list[uuid.UUID] = Field(default_factory=list)
    # Vocabulary item ids, so "which hazards keep coming up" is answerable.
    identified_hazard_ids: list[uuid.UUID] = Field(default_factory=list)
    ppe_that_could_prevent: list[uuid.UUID] = Field(default_factory=list)
    sequence_of_events: str | None = None
    root_cause_narrative: str | None = None


class InvestigationSignIn(BaseModel):
    signature: str = Field(min_length=1, description="base64 PNG of the drawn signature")


class CauseActionIn(BaseModel):
    """A corrective action raised with the cause it addresses, in one request.

    ★ Round 4: "when adding a root cause on any form, the ability to raise a
    corrective action should be able to be completed on the same form at the same
    time. having to save the record first and then edit the corrective action is
    time wasting."

    It has to be one request rather than two because an action points at a cause
    and a cause has no id until it is saved. Doing it in the client would mean
    saving the causes, reading back which id belongs to which of the rows just
    typed, and then posting the actions — three steps whose middle one is a
    guess. The server already has the correspondence.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    # ★ Required, as it is everywhere an action is raised. "A failed check with
    # nobody assigned to it is how a known hazard stays a known hazard" — an
    # unowned action has no inbox to appear in and no escalation ladder to climb.
    owner_id: uuid.UUID
    due_date: date
    hierarchy_of_control: str | None = None
    # ★ Round 5, TC-04: "make the 'What is being done about it' form look the
    # same as the 'Raise a corrective action' form to ensure consistency." The
    # dialog asked for a priority and this did not, so the same action raised two
    # ways came out different. Same field, same default.
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")


class CauseIn(BaseModel):
    cause_type: str = Field(pattern="^(immediate|root)$")
    vocabulary_item_id: uuid.UUID | None = None
    # Snapshot of the wording at the time. Required even when a vocabulary item
    # is given, because the vocabulary will be relabelled over the years and a
    # signed investigation has to keep reading the way it was signed.
    label: str = Field(min_length=1, max_length=200)
    note: str | None = None
    # Raised with it. Ignored when this cause already has one of the same title,
    # so saving the investigation a second time does not raise a duplicate.
    action: CauseActionIn | None = None


class CausesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """The investigation screen edits causes as a set, not one at a time."""

    causes: list[CauseIn]


class InvestigationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    investigator_id: uuid.UUID | None
    investigator_name: str | None
    supporting_investigator_ids: list[uuid.UUID]
    identified_hazard_ids: list[uuid.UUID]
    ppe_that_could_prevent: list[uuid.UUID]
    sequence_of_events: str | None
    root_cause_narrative: str | None
    completed_at: datetime | None
    signed_by_name: str | None


class CauseActionOut(BaseModel):
    """An action as it appears hanging under the cause it addresses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_no: str
    title: str
    # ★ What the action actually says to do. Round 4: "any root cause analysis,
    # or capa completed as part of the drill or on any form shall display the
    # capa description". A title is a label — "Guard the nip point" — and the
    # description is the instruction, which is the part somebody reading the
    # root cause needs in order to judge whether the cause has been answered.
    description: str | None = None
    status: str
    owner_name: str | None
    due_date: object
    escalation_level: int


class CauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cause_type: str
    vocabulary_item_id: uuid.UUID | None
    label: str
    note: str | None
    sort_order: int
    actions: list[CauseActionOut] = Field(default_factory=list)
    # True when a root cause has nothing being done about it. The detail screen
    # renders these as visibly incomplete, which is what an auditor looks for.
    needs_action: bool = False


class CauseTreeOut(BaseModel):
    incident_id: uuid.UUID
    incident_no: str
    immediate: list[CauseOut] = Field(default_factory=list)
    root: list[CauseOut] = Field(default_factory=list)
    # Root causes with no corrective action attached.
    unaddressed_root_causes: int = 0
