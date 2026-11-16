"""Toolbox talks: the monthly topic and the meetings that deliver it."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.crud import toolbox as crud
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server

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
    material_filename: str | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    talk_id: uuid.UUID
    supervisor_name: str | None
    department_name: str | None
    status: str
    # When it was handed over, which is the other half of "is this late":
    # a delivery outstanding for a day and one outstanding for six weeks read
    # the same without it.
    created_at: datetime
    held_at: datetime | None
    expected_attendees: int
    signed_count: int
    # UAT 13: set on a talk held again — the delivery it repeats.
    rerun_of: uuid.UUID | None = None


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


MATERIAL_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


class PublishIn(BaseModel):
    """Who to hand it to. Empty or absent means everybody, which is the norm."""

    supervisor_ids: list[uuid.UUID] = Field(default_factory=list)


class SupervisorOut(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str


@router.get("", response_model=list[TalkOut])
async def list_talks(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return await crud.list_talks(db)


@router.post("", response_model=TalkOut, status_code=201)
async def create_talk(payload: TalkIn, db: SessionDep, user: CanManageSettings):
    return await crud.create_talk(
        db, title=payload.title, topic=payload.topic,
        period_month=payload.period_month, creator_id=_user_id(user))


@router.post("/{talk_id}/publish", response_model=list[DeliveryOut])
async def publish(
    talk_id: uuid.UUID, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
    payload: PublishIn | None = None,
):
    """Hand it to every supervisor and open their task."""
    talk = await crud.get_talk(db, talk_id)
    await crud.publish_talk(db, talk, only_to=payload.supervisor_ids if payload else None)
    return await crud.list_deliveries(db, talk_id=talk_id)


@router.post("/{talk_id}/hold-again", response_model=DeliveryOut, status_code=201)
async def hold_again(talk_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """★ UAT 13: run a past talk again, with a new attendance sheet.

    For those who hold talks: a supervisor (any of the supervisory roles), HSE,
    or anybody a talk has been handed to before — a lead hand runs a crew
    without a supervisor's role."""
    from sqlalchemy import select as _select
    from uniops_authz import has_permission

    from app.models.mirrors import User
    from app.models.toolbox import TbtDelivery
    from app.services.notify import roles_held_by
    me = _user_id(user)
    talk = await crud.get_talk(db, talk_id)
    if not talk.is_published:
        raise HTTPException(status.HTTP_409_CONFLICT, "This talk has not been handed out yet")
    roles = await roles_held_by(db, me, user.get("role", ""))
    may = (bool(set(crud.SUPERVISORY_ROLES) & roles)
           or await has_permission(db, me, user.get("role", ""), "ehs.settings.manage")
           or (await db.execute(_select(TbtDelivery.id).where(
               TbtDelivery.supervisor_id == me).limit(1))).first() is not None)
    if not may:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Holding a toolbox talk is for those who run a crew")
    person = await db.get(User, me)
    return await crud.hold_again(db, talk, person=person)


@router.get("/supervisors", response_model=list[SupervisorOut])
async def list_supervisors(
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
    everyone: bool = False,
):
    """Who a talk can be sent to, so it can be sent to some of them.

    Every supervisor is the right default — the talk is monthly and plant-wide
    — but a topic that concerns two crews sent to forty people trains
    thirty-eight of them to ignore the next one.

    `everyone` offers the whole staff list instead. A lead hand runs a crew and
    holds no supervisory role, and until the second acceptance round asked for
    it there was no way to hand them the sheet.
    """
    people = (await crud.all_staff(db)) if everyone else (
        await crud.supervisors_for_talks(db))
    return [
        SupervisorOut(id=u.id, full_name=u.full_name, role=u.role)
        for u in people
    ]


@router.post("/{talk_id}/material", response_model=TalkOut)
async def attach_material(
    talk_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanManageSettings, token: BearerToken,  # noqa: ARG001
):
    """The month's sheet. The topic *is* a one-page PDF."""
    talk = await crud.get_talk(db, talk_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in MATERIAL_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: PDF or an image.")
    talk.material_file_id = await upload_to_file_server(
        data, file.filename or "toolbox-talk.pdf", ctype, "ehs_tbt", talk.id, token)
    talk.material_filename = file.filename or "toolbox-talk.pdf"
    await db.flush()
    return talk


@router.get("/{talk_id}/material")
async def download_material(
    talk_id: uuid.UUID, db: SessionDep, user: CanReportIncident, token: BearerToken,  # noqa: ARG001
):
    """Readable by everyone: the crew signs against what it says."""
    talk = await crud.get_talk(db, talk_id)
    if talk.material_file_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"{talk.talk_no} has no sheet attached yet")
    return await proxy_download(talk.material_file_id, token)


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


# ── Round 12, TBT-01/02: the monthly tracker and chasing by hand ───────────

class TrackerDelivery(BaseModel):
    id: uuid.UUID
    talk_no: str
    title: str
    department_name: str | None
    status: str
    held_at: datetime | None
    last_reminded_on: date | None


class TrackerRow(BaseModel):
    person_id: uuid.UUID | None
    person_name: str | None
    assigned: int
    delivered: int
    outstanding: int
    last_reminded_on: date | None
    deliveries: list[TrackerDelivery]


class TrackerPage(BaseModel):
    month: date
    items: list[TrackerRow]
    total: int
    # The whole month, not this page: what the header says.
    assigned: int
    delivered: int
    outstanding: int


@router.get("/tracker", response_model=TrackerPage)
async def tracker(
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
    month: date = Query(description="Any day of the month; the month is what is used"),
    q: str | None = Query(default=None, max_length=100, description="Person's name"),
    sort: str | None = None,
    dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """★ Round 12, TBT-01: "track on a monthly basis the toolbox talks assigned
    to each person and whether or not they have completed them." HSE's — the
    same people who hand the talks out."""
    items, total, sums = await crud.tracker(
        db, month=month, q=q, sort=sort, dir=dir,
        offset=(page - 1) * page_size, limit=page_size)
    return TrackerPage(month=crud.month_start(month), items=items, total=total, **sums)


class RemindedOut(BaseModel):
    reminded: int
    # Review C4/C5: not sent again because already chased today; not sent
    # because nobody on it has an address.
    already_today: int = 0
    nobody_to_send_to: int = 0


@router.post("/deliveries/{delivery_id}/remind", response_model=RemindedOut)
async def remind_one(delivery_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """★ Round 12, TBT-02: chase one outstanding talk now."""
    delivery = await crud.get_delivery(db, delivery_id)
    what = await crud.remind(db, delivery)
    if what == "delivered":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This talk has been delivered; there is nothing to chase.")
    if what == "nobody":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Nobody with an email address is on this talk, so no reminder "
                            "was sent. Hand it to somebody under Reallocate Items.")
    return RemindedOut(reminded=int(what == "sent"), already_today=int(what == "today"))


@router.post("/remind-all", response_model=RemindedOut)
async def remind_all(
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
    month: date = Query(description="Any day of the month whose outstanding talks to chase"),
):
    """★ Round 12, TBT-02: "remind everyone" — every talk of that month not yet
    signed off."""
    got = await crud.remind_all(db, month=month)
    return RemindedOut(reminded=got["sent"], already_today=got["today"],
                       nobody_to_send_to=got["nobody"])


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
