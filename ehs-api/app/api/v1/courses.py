"""Course sessions: schedule one, build its roster, mark who passed.

Authorisation here is not a single key, because the process is not run by a
single person. HSE schedules the session and decides who passed — both
`ehs.training.write`. The supervisor HSE nominated builds the roster, and
holds only `ehs.training.read`. So roster edits are gated by an ownership
check rather than by a permission: this session's coordinator, or anyone who
could have created it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanReadTraining, CanWriteTraining
from app.crud import course as crud
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server
from uniops_authz import has_permission

router = APIRouter(prefix="/course-sessions")


class DeliveryIn(BaseModel):
    course_id: uuid.UUID
    # Defaults to the course name; set it when this sitting covers a variant.
    title: str | None = Field(default=None, max_length=200)
    session_date: date
    start_time: time | None = None
    end_time: time | None = None
    location_text: str | None = Field(default=None, max_length=200)
    location_id: uuid.UUID | None = None
    coordinator_id: uuid.UUID | None = None
    notes: str | None = None


class DeliveryPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    location_text: str | None = Field(default=None, max_length=200)
    location_id: uuid.UUID | None = None
    coordinator_id: uuid.UUID | None = None
    instructor_name: str | None = Field(default=None, max_length=200)
    material_file_id: uuid.UUID | None = None
    material_filename: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    delivery_no: str
    course_id: uuid.UUID | None
    course_label: str
    title: str
    session_date: date
    start_time: time | None
    end_time: time | None
    location_text: str | None
    coordinator_id: uuid.UUID | None
    coordinator_name: str | None
    instructor_name: str | None
    material_file_id: uuid.UUID | None
    material_filename: str | None
    status: str
    notes: str | None
    enrolled: int = 0
    passed: int = 0


class EnrolmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    department_name: str | None
    result: str
    marked_by_name: str | None
    marked_at: datetime | None
    training_record_id: uuid.UUID | None


class EnrolIn(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ResultIn(BaseModel):
    enrolment_id: uuid.UUID
    result: str = Field(pattern="^(passed|failed|absent|enrolled)$")


class MarkIn(BaseModel):
    results: list[ResultIn] = Field(min_length=1)
    # Marking is usually the last thing that happens, so this saves a second
    # call. Left false when HSE is marking part of a roster as they go.
    complete: bool = False


async def _with_counts(db, deliveries) -> list[DeliveryOut]:
    out: list[DeliveryOut] = []
    for delivery in deliveries:
        rows = await crud.roster(db, delivery.id)
        item = DeliveryOut.model_validate(delivery)
        item.enrolled = len(rows)
        item.passed = sum(1 for r in rows if r.result == "passed")
        out.append(item)
    return out


async def _require_roster_rights(db, user: dict, delivery) -> None:
    """The nominated coordinator, or anyone who may manage training at all."""
    uid = _user_id(user)
    if delivery.coordinator_id == uid:
        return
    if await has_permission(db, uid, user.get("role", ""), "ehs.training.write"):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"{delivery.coordinator_name or 'Another supervisor'} is running this session; "
        "only they or HSE can change its roster")


@router.get("", response_model=list[DeliveryOut])
async def list_sessions(
    db: SessionDep, user: CanReadTraining,
    course_id: uuid.UUID | None = None,
    mine: bool = Query(default=False, description="Sessions I have been asked to run"),
    attending: bool = Query(default=False, description="Sessions I am booked on"),
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    uid = _user_id(user)
    return await _with_counts(db, await crud.list_deliveries(
        db, course_id=course_id,
        coordinator_id=uid if mine else None,
        attendee_id=uid if attending else None,
        status_in=status_in))


@router.post("", response_model=DeliveryOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: DeliveryIn, db: SessionDep, user: CanWriteTraining):
    delivery = await crud.create_delivery(
        db, course_id=payload.course_id, title=payload.title,
        session_date=payload.session_date, start_time=payload.start_time,
        end_time=payload.end_time, location_text=payload.location_text,
        location_id=payload.location_id, coordinator_id=payload.coordinator_id,
        notes=payload.notes, creator_id=_user_id(user))
    return (await _with_counts(db, [delivery]))[0]


@router.get("/{delivery_id}", response_model=DeliveryOut)
async def get_session(delivery_id: uuid.UUID, db: SessionDep, user: CanReadTraining):  # noqa: ARG001
    return (await _with_counts(db, [await crud.get_delivery(db, delivery_id)]))[0]


@router.patch("/{delivery_id}", response_model=DeliveryOut)
async def update_session(
    delivery_id: uuid.UUID, payload: DeliveryPatch, db: SessionDep, user: CanWriteTraining,  # noqa: ARG001
):
    delivery = await crud.get_delivery(db, delivery_id)
    fields = payload.model_dump(exclude_unset=True)
    # Reassigning has to move the task as well, so it goes through crud rather
    # than being set like the rest.
    if "coordinator_id" in fields:
        await crud.set_coordinator(db, delivery, fields.pop("coordinator_id"))
    for key, value in fields.items():
        setattr(delivery, key, value)
    await db.flush()
    return (await _with_counts(db, [delivery]))[0]


@router.get("/{delivery_id}/roster", response_model=list[EnrolmentOut])
async def get_roster(delivery_id: uuid.UUID, db: SessionDep, user: CanReadTraining):  # noqa: ARG001
    await crud.get_delivery(db, delivery_id)
    return await crud.roster(db, delivery_id)


@router.post("/{delivery_id}/roster", response_model=list[EnrolmentOut],
             status_code=status.HTTP_201_CREATED)
async def add_to_roster(
    delivery_id: uuid.UUID, payload: EnrolIn, db: SessionDep, user: CanReadTraining,
):
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_roster_rights(db, user, delivery)
    return await crud.enrol(db, delivery, payload.user_ids)


@router.delete("/{delivery_id}/roster/{enrolment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_roster(
    delivery_id: uuid.UUID, enrolment_id: uuid.UUID, db: SessionDep, user: CanReadTraining,
):
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_roster_rights(db, user, delivery)
    await crud.withdraw(db, delivery, enrolment_id)


@router.post("/{delivery_id}/results", response_model=list[EnrolmentOut])
async def mark_results(
    delivery_id: uuid.UUID, payload: MarkIn, db: SessionDep, user: CanWriteTraining,
):
    """Record who passed. Each pass writes a row into the training register."""
    delivery = await crud.get_delivery(db, delivery_id)
    if delivery.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This session was cancelled")
    changed = await crud.mark_results(
        db, delivery, {r.enrolment_id: r.result for r in payload.results},
        marker_id=_user_id(user))
    if payload.complete:
        await crud.complete(db, delivery)
    return changed


@router.post("/{delivery_id}/complete", response_model=DeliveryOut)
async def complete_session(delivery_id: uuid.UUID, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    delivery = await crud.complete(db, await crud.get_delivery(db, delivery_id))
    return (await _with_counts(db, [delivery]))[0]


@router.post("/{delivery_id}/cancel", response_model=DeliveryOut)
async def cancel_session(delivery_id: uuid.UUID, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    delivery = await crud.cancel(db, await crud.get_delivery(db, delivery_id))
    return (await _with_counts(db, [delivery]))[0]


@router.post("/{delivery_id}/material", response_model=DeliveryOut)
async def attach_material(
    delivery_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanWriteTraining, token: BearerToken,  # noqa: ARG001
):
    """The handout or deck for this sitting.

    Sits on the session rather than only on the course because a session often
    uses a revised deck, and the register has to show what was actually taught.
    """
    delivery = await crud.get_delivery(db, delivery_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    delivery.material_file_id = await upload_to_file_server(
        data, file.filename or "material.pdf",
        (file.content_type or "application/pdf").split(";")[0].strip().lower(),
        "ehs_crs", delivery.id, token)
    delivery.material_filename = file.filename or "material.pdf"
    await db.flush()
    return (await _with_counts(db, [delivery]))[0]


@router.get("/{delivery_id}/material")
async def download_material(
    delivery_id: uuid.UUID, db: SessionDep, user: CanReadTraining, token: BearerToken,  # noqa: ARG001
):
    delivery = await crud.get_delivery(db, delivery_id)
    if delivery.material_file_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No material attached to this session")
    return await proxy_download(delivery.material_file_id, token)
