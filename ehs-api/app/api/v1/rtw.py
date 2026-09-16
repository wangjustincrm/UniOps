"""Return-to-work plans, hung off the incident that caused them.

Gated on `ehs.incident.medical.read` rather than on incident read. A plan says
what a named person can and cannot physically do; that is health information
about an identified individual, and the fact that it lives on an incident does
not make it less so.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadMedical
from app.crud import incident as incident_crud
from app.crud import rtw as crud

router = APIRouter(prefix="/incidents/{incident_id}/return-to-work")


class PlanIn(BaseModel):
    # Defaults to whoever is recorded as injured on the incident.
    worker_user_id: uuid.UUID | None = None
    worker_name: str | None = Field(default=None, max_length=255)
    # {"lifting": "10kg max", "standing": "30 min", ...}
    functional_abilities: dict | None = None
    restrictions: str | None = None
    modified_duties: str | None = None
    # Left null on purpose when nobody has said. HSE's lost-time rule turns on
    # this, and recording a guess as a fact is how a WSIB filing goes out wrong.
    at_regular_pay: bool | None = None
    start_date: date | None = None
    expected_end_date: date | None = None


class PlanPatch(PlanIn):
    status: str | None = Field(default=None, pattern="^(active|completed|abandoned)$")
    actual_end_date: date | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    worker_user_id: uuid.UUID | None
    worker_name: str
    functional_abilities: dict
    restrictions: str | None
    modified_duties: str | None
    at_regular_pay: bool | None
    start_date: date
    expected_end_date: date | None
    actual_end_date: date | None
    status: str
    created_at: datetime


class CheckinIn(BaseModel):
    notes: str = Field(min_length=1)
    checkin_date: date | None = None


class CheckinOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checkin_date: date
    notes: str | None
    by_name: str | None
    created_at: datetime


class CloseIn(BaseModel):
    actual_end_date: date | None = None
    outcome: str = Field(default="completed", pattern="^(completed|abandoned)$")


@router.get("", response_model=list[PlanOut])
async def list_plans(incident_id: uuid.UUID, db: SessionDep, user: CanReadMedical):  # noqa: ARG001
    await incident_crud.get(db, incident_id)
    return await crud.plans_for(db, incident_id)


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    incident_id: uuid.UUID, payload: PlanIn, db: SessionDep, user: CanReadMedical,  # noqa: ARG001
):
    """Open a plan. The worker defaults to whoever the incident records as hurt.

    Deliberately not refused on a closed incident, unlike the rest of the
    record. Modified duties routinely run for weeks after the incident itself
    is finished, and the plan is a record about the person rather than a part
    of the incident — freezing it with the incident would leave a live
    return-to-work with nowhere to record its check-ins, and somebody would
    keep it in a spreadsheet instead.
    """
    incident = await incident_crud.get(db, incident_id)
    return await crud.create_plan(db, incident, payload.model_dump(exclude_unset=True))


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    incident_id: uuid.UUID, plan_id: uuid.UUID, payload: PlanPatch,  # noqa: ARG001
    db: SessionDep, user: CanReadMedical,  # noqa: ARG001
):
    plan = await crud.get_plan(db, plan_id)
    return await crud.update_plan(db, plan, payload.model_dump(exclude_unset=True))


@router.post("/{plan_id}/close", response_model=PlanOut)
async def close_plan(
    incident_id: uuid.UUID, plan_id: uuid.UUID, payload: CloseIn,  # noqa: ARG001
    db: SessionDep, user: CanReadMedical,  # noqa: ARG001
):
    """Back on full duties, or the plan ended some other way."""
    plan = await crud.get_plan(db, plan_id)
    return await crud.close_plan(
        db, plan, on=payload.actual_end_date, outcome=payload.outcome)


@router.get("/{plan_id}/checkins", response_model=list[CheckinOut])
async def list_checkins(
    incident_id: uuid.UUID, plan_id: uuid.UUID,  # noqa: ARG001
    db: SessionDep, user: CanReadMedical,  # noqa: ARG001
):
    await crud.get_plan(db, plan_id)
    return await crud.checkins_for(db, plan_id)


@router.post("/{plan_id}/checkins", response_model=CheckinOut,
             status_code=status.HTTP_201_CREATED)
async def add_checkin(
    incident_id: uuid.UUID, plan_id: uuid.UUID, payload: CheckinIn,  # noqa: ARG001
    db: SessionDep, user: CanReadMedical,
):
    """The only evidence that anybody looked again."""
    plan = await crud.get_plan(db, plan_id)
    return await crud.add_checkin(
        db, plan, notes=payload.notes, on=payload.checkin_date,
        by_user_id=_user_id(user))
