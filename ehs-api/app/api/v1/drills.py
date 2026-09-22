"""Emergency drills, and who is qualified to respond.

Reading sits on the incident read key — a drill record is the same kind of
thing as an incident record, read by the same people. Running one sits with
first-aid recording, because the people who organise a drill are the people who
staff the emergency response.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident, CanWriteFirstAid
from app.crud import drill as crud
from app.crud.incident import _resolve_name

router = APIRouter()

_TYPES = "^(fire|evacuation|spill|rescue|medical|shelter|other)$"


class DrillIn(BaseModel):
    drill_type: str = Field(pattern=_TYPES)
    scenario: str = Field(min_length=1)
    planned_for: date | None = None
    held_on: date | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    area_ids: list[uuid.UUID] = Field(default_factory=list)
    # Announced drills measure the procedure; unannounced ones measure the
    # people. They are not comparable, so the record says which it was.
    is_announced: bool = True


class DrillPatch(BaseModel):
    # An unknown key is a mistake, not something to drop quietly — the same
    # reason the incident and medical write models refuse them.
    model_config = ConfigDict(extra="forbid")

    scenario: str | None = None
    planned_for: date | None = None
    held_on: date | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    area_ids: list[uuid.UUID] | None = None
    is_announced: bool | None = None
    participants_count: int | None = Field(default=None, ge=0)
    # Left unset, it is derived from the two clock times.
    evacuation_seconds: int | None = Field(default=None, ge=0, le=86_400)
    all_accounted_for: bool | None = None
    missing_note: str | None = Field(default=None, max_length=300)
    observations: str | None = None
    what_worked: str | None = None
    what_failed: str | None = None
    # Why it went the way it did, from the same list the investigations use.
    root_cause_id: uuid.UUID | None = None
    root_cause_label: str | None = Field(default=None, max_length=200)
    # Base64 PNG. Signing stamps the name and time on the server — a signature
    # whose "who" and "when" came from the client is not evidence of anything.
    signature: str | None = None


class DrillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    drill_no: str
    drill_type: str
    scenario: str
    planned_for: date | None
    held_on: date | None
    started_at: datetime | None
    ended_at: datetime | None
    areas: list
    is_announced: bool
    participants_count: int | None
    evacuation_seconds: int | None
    all_accounted_for: bool | None
    missing_note: str | None
    observations: str | None
    root_cause_id: uuid.UUID | None
    root_cause_label: str | None
    signature: str | None
    signed_by_name: str | None
    signed_at: datetime | None
    what_worked: str | None
    what_failed: str | None
    status: str
    evaluated_at: datetime | None
    evaluated_by_name: str | None
    created_by_name: str | None


class ParticipantIn(BaseModel):
    user_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=255)
    capacity: str = Field(default="participant",
                          pattern="^(participant|warden|first_aider|controller|observer)$")
    note: str | None = Field(default=None, max_length=300)


class ParticipantsIn(BaseModel):
    people: list[ParticipantIn] = Field(min_length=1)


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    person_name: str
    capacity: str
    note: str | None


class TrendRow(BaseModel):
    drill_no: str
    held_on: str | None
    seconds: int
    announced: bool
    all_accounted_for: bool | None


class Responder(BaseModel):
    user_id: str
    user_name: str
    shift_code: str | None
    certified_on: str | None
    expires_on: str | None
    is_current: bool
    # Holds the first-aider role but has no record in the register — usually a
    # ticket earned before the register existed.
    designated_only: bool


class ShiftCount(BaseModel):
    shift: str
    count: int


class CoverageOut(BaseModel):
    total: int
    current: int
    lapsed: list[dict]
    by_shift: list[ShiftCount]
    shift_unknown: int


@router.get("/drills", response_model=list[DrillOut])
async def list_drills(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
    drill_type: str | None = Query(default=None, pattern=_TYPES),
    year: int | None = Query(default=None, ge=2000, le=2100),
):
    return await crud.list_drills(
        db, status_in=status_in, drill_type=drill_type, year=year)


@router.get("/drills/trend", response_model=list[TrendRow])
async def drill_trend(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    drill_type: str = Query(default="fire", pattern=_TYPES),
):
    """Evacuation times, oldest first. A single time means nothing."""
    return await crud.trend(db, drill_type=drill_type)


@router.get("/emergency/responders", response_model=list[Responder])
async def list_responders(db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Qualified first aiders, computed from the training register rather than
    kept as a second list that would drift from it."""
    return await crud.responders(db)


@router.get("/emergency/coverage", response_model=CoverageOut)
async def responder_coverage(db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Is every shift covered — what Reg 1101 asks."""
    return await crud.coverage(db)


@router.post("/drills", response_model=DrillOut, status_code=status.HTTP_201_CREATED)
async def create_drill(payload: DrillIn, db: SessionDep, user: CanWriteFirstAid):
    return await crud.create_drill(db, payload.model_dump(), creator_id=_user_id(user))


@router.get("/drills/{drill_id}", response_model=DrillOut)
async def get_drill(drill_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    return await crud.get_drill(db, drill_id)


@router.patch("/drills/{drill_id}", response_model=DrillOut)
async def update_drill(
    drill_id: uuid.UUID, payload: DrillPatch, db: SessionDep, user: CanWriteFirstAid,  # noqa: ARG001
):
    drill = await crud.get_drill(db, drill_id)
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("signature"):
        # Stamped here, never taken from the caller. A signature carrying a
        # name and a time the client chose is not evidence of anything, and
        # this is the record an inspector is handed.
        fields["signed_by"] = _user_id(user)
        fields["signed_by_name"] = await _resolve_name(db, _user_id(user))
        fields["signed_at"] = datetime.now(timezone.utc)
    elif "signature" in fields:
        # Cleared on purpose — the drill is unsigned again, and leaving the
        # name behind would show it as signed by somebody with nothing under it.
        fields.update(signed_by=None, signed_by_name=None, signed_at=None)
    return await crud.update_drill(db, drill, fields)


@router.post("/drills/{drill_id}/evaluate", response_model=DrillOut)
async def evaluate_drill(drill_id: uuid.UUID, db: SessionDep, user: CanWriteFirstAid):
    """Close it off. Refused until the muster question is answered."""
    drill = await crud.get_drill(db, drill_id)
    return await crud.evaluate(db, drill, user_id=_user_id(user))


@router.get("/drills/{drill_id}/participants", response_model=list[ParticipantOut])
async def list_participants(
    drill_id: uuid.UUID, db: SessionDep, user: CanReadIncident,  # noqa: ARG001
):
    await crud.get_drill(db, drill_id)
    return await crud.participants_for(db, drill_id)


@router.post("/drills/{drill_id}/participants", response_model=list[ParticipantOut],
             status_code=status.HTTP_201_CREATED)
async def add_participants(
    drill_id: uuid.UUID, payload: ParticipantsIn, db: SessionDep, user: CanWriteFirstAid,  # noqa: ARG001
):
    """Named participation is for the people whose role is itself being tested
    — wardens, first aiders, the controller. Everyone else is a headcount."""
    drill = await crud.get_drill(db, drill_id)
    return await crud.add_participants(db, drill, [p.model_dump() for p in payload.people])
