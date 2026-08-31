"""Toolbox talks: the monthly topic and the meetings that deliver it."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.crud import toolbox as crud

router = APIRouter(prefix="/toolbox-talks")


class TalkIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str | None = None
    # Any day in the month; stored as its first.
    period_month: date


class TalkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    talk_no: str
    title: str
    topic: str | None
    period_month: date
    is_published: bool


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    talk_id: uuid.UUID
    supervisor_name: str | None
    department_name: str | None
    status: str
    held_at: datetime | None
    expected_attendees: int
    signed_count: int


class SignatureIn(BaseModel):
    user_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=255)
    person_role: str | None = Field(default=None, max_length=150)
    signature: str = Field(min_length=1)


class SignOffIn(BaseModel):
    signatures: list[SignatureIn] = Field(default_factory=list)
    # Present = the meeting happened and this closes it. Absent = signatures are
    # still being collected, which is what a latecomer signing later looks like.
    held_at: datetime | None = None
    expected_attendees: int | None = Field(default=None, ge=0, le=500)


class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_name: str
    person_role: str | None
    signed_at: datetime


class CompletionOut(BaseModel):
    total: int
    delivered: int
    pending: int
    percent: int


@router.get("", response_model=list[TalkOut])
async def list_talks(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return await crud.list_talks(db)


@router.post("", response_model=TalkOut, status_code=201)
async def create_talk(payload: TalkIn, db: SessionDep, user: CanManageSettings):
    return await crud.create_talk(
        db, title=payload.title, topic=payload.topic,
        period_month=payload.period_month, creator_id=_user_id(user))


@router.post("/{talk_id}/publish", response_model=list[DeliveryOut])
async def publish(talk_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Hand it to every supervisor and open their task."""
    talk = await crud.get_talk(db, talk_id)
    await crud.publish_talk(db, talk)
    return await crud.list_deliveries(db, talk_id=talk_id)


@router.get("/{talk_id}/completion", response_model=CompletionOut)
async def completion(talk_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    await crud.get_talk(db, talk_id)
    return await crud.completion(db, talk_id)


@router.get("/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    db: SessionDep, user: CanReportIncident,
    talk_id: uuid.UUID | None = None,
    mine: bool = Query(default=False, description="Only the ones I have to deliver"),
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    return await crud.list_deliveries(
        db, talk_id=talk_id,
        supervisor_id=_user_id(user) if mine else None,
        status_in=status_in)


@router.get("/deliveries/{delivery_id}/signatures", response_model=list[SignatureOut])
async def list_signatures(delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    await crud.get_delivery(db, delivery_id)
    return await crud.signatures_for(db, delivery_id)


@router.post("/deliveries/{delivery_id}/sign", response_model=DeliveryOut)
async def sign_off(
    delivery_id: uuid.UUID, payload: SignOffIn, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
):
    """Record the meeting's signatures — several at once, from one device."""
    delivery = await crud.get_delivery(db, delivery_id)
    if payload.expected_attendees is not None:
        delivery.expected_attendees = payload.expected_attendees
    return await crud.add_signatures(
        db, delivery,
        [s.model_dump() for s in payload.signatures],
        held_at=payload.held_at)
