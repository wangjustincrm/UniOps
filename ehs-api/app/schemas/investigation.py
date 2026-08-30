"""Investigation, causes, and the cause tree the detail screen renders."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InvestigationIn(BaseModel):
    investigator_id: uuid.UUID | None = None
    supporting_investigator_ids: list[uuid.UUID] = Field(default_factory=list)
    # Vocabulary item ids, so "which hazards keep coming up" is answerable.
    identified_hazard_ids: list[uuid.UUID] = Field(default_factory=list)
    ppe_that_could_prevent: list[uuid.UUID] = Field(default_factory=list)
    sequence_of_events: str | None = None
    root_cause_narrative: str | None = None


class InvestigationSignIn(BaseModel):
    signature: str = Field(min_length=1, description="base64 PNG of the drawn signature")


class CauseIn(BaseModel):
    cause_type: str = Field(pattern="^(immediate|root)$")
    vocabulary_item_id: uuid.UUID | None = None
    # Snapshot of the wording at the time. Required even when a vocabulary item
    # is given, because the vocabulary will be relabelled over the years and a
    # signed investigation has to keep reading the way it was signed.
    label: str = Field(min_length=1, max_length=200)
    note: str | None = None


class CausesReplace(BaseModel):
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
