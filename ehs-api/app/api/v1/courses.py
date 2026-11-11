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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanReportIncident, CanWriteTraining
from app.crud import course as crud
from app.models.course import CourseMaterial
from app.models.quiz import QuizAttempt
from app.services import materials
from app.services.attachments import proxy_download
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

    @field_validator("session_date")
    @classmethod
    def _a_session_has_a_date(cls, v):
        """Absent is "leave it"; an explicit null would clear a NOT NULL column
        and answer 500. Raised in review by uniops-41."""
        if v is None:
            raise ValueError("A session needs a date")
        return v


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
    reopened_at: datetime | None = None
    reopened_by_name: str | None = None
    reopen_reason: str | None = None
    enrolled: int = 0
    passed: int = 0
    # The quiz this session is tested on, when its course has one. Round 6:
    # "place the QR code for the quiz on the course session."
    quiz_id: uuid.UUID | None = None
    quiz_title: str | None = None
    quiz_qr_token: str | None = None
    # From the start and end times. Round 6: "track the length of time the
    # training is ... this course length also needs to be on any reports."
    length_minutes: int | None = None
    end_time_from_quiz: bool = False


class EnrolmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    department_name: str | None
    result: str
    marked_by_name: str | None
    # roster | quiz — quiz means they scanned the code in the room.
    joined_via: str = "roster"
    # "Quiz passed": when, and by whom — "Online quiz" or the person running it.
    quiz_passed_at: datetime | None = None
    quiz_marked_by_name: str | None = None
    marked_at: datetime | None
    training_record_id: uuid.UUID | None


class EnrolIn(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ResultIn(BaseModel):
    enrolment_id: uuid.UUID
    result: str = Field(pattern="^(passed|failed|absent|enrolled)$")


class MarkIn(BaseModel):
    # ★ May be empty when closing. Round 6: with every attendee already marked,
    # "Save and close the session" sent no changes and was refused 422 — so a
    # fully marked session could never be closed. Empty is still refused when
    # nothing is being closed, because then the request does nothing at all.
    results: list[ResultIn] = Field(default_factory=list)
    # Marking is usually the last thing that happens, so this saves a second
    # call. Left false when HSE is marking part of a roster as they go.
    complete: bool = False
    # Close even though somebody who passed the quiz is still unmarked, which
    # records them as not attended. Round 6 review.
    close_anyway: bool = False

    @model_validator(mode="after")
    def _something_to_do(self):
        if not self.results and not self.complete:
            raise ValueError("Nothing to save: mark somebody, or close the session")
        return self


async def _with_counts(db, deliveries) -> list[DeliveryOut]:
    out: list[DeliveryOut] = []
    for delivery in deliveries:
        rows = await crud.roster(db, delivery.id)
        item = DeliveryOut.model_validate(delivery)
        item.enrolled = len(rows)
        item.passed = sum(1 for r in rows if r.result == "passed")
        item.length_minutes = crud.length_minutes(delivery)
        quiz = await crud.quiz_for_course(db, delivery.course_id)
        if quiz is not None:
            item.quiz_id, item.quiz_title, item.quiz_qr_token = quiz.id, quiz.title, quiz.qr_token
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


async def _require_session_access(db, user: dict, delivery) -> None:
    """Whoever is running it, or anyone who may read the training register.

    ★ Round 4, TC-09 and TC-10: "the run by person on training should have full
    edit and create rights to allow for adding and marking students … allow the
    run by person to pass or fail the students as that role is currently limit to
    the HSE role."

    The gate on these endpoints was `ehs.training.read`, and the coordinator is
    whoever HSE nominated — on the dev database that includes an ordinary
    employee, and `requester` does not hold that permission. So the person named
    to run a session could not open it, let alone mark it. `supervisor` and
    `dept_manager` hold no training permission either.

    Being named to run the session is the right to see it. The register as a
    whole still needs the register's permission.
    """
    uid = _user_id(user)
    if delivery.coordinator_id == uid:
        return
    if await has_permission(db, uid, user.get("role", ""), "ehs.training.read"):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "You are not running this session and do not have access to the "
        "training register")


@router.get("", response_model=list[DeliveryOut])
async def list_sessions(
    db: SessionDep, user: CanReportIncident,
    course_id: uuid.UUID | None = None,
    mine: bool = Query(default=False, description="Sessions I have been asked to run"),
    attending: bool = Query(default=False, description="Sessions I am booked on"),
    status_in: list[str] | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100,
                               description="Round 12, TRN-04: session number or title"),
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Sessions. Everybody may list their own; the whole list needs training-read.

    ★ Round 6, TC-16: "a worker can not add attendees or manage courses they are
    running — Insufficient permissions." The person HSE names to run a session
    is usually an ordinary employee, and the only list of sessions was behind
    `ehs.training.read`, so the one they run could be reached from a task in My
    Actions and from nowhere else. `mine` and `attending` are about the caller
    and nobody else, so they are open to the caller.
    """
    uid = _user_id(user)
    if not (mine or attending) and not await has_permission(
            db, uid, user.get("role", ""), "ehs.training.read"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Listing every session needs the training-read permission; "
            "the sessions you run are under My Training")
    return await _with_counts(db, await crud.list_deliveries(
        db, course_id=course_id,
        coordinator_id=uid if mine else None,
        attendee_id=uid if attending else None,
        status_in=status_in, search=search, date_from=date_from, date_to=date_to))


@router.post("", response_model=DeliveryOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: DeliveryIn, db: SessionDep, user: CanWriteTraining):
    delivery = await crud.create_delivery(
        db, course_id=payload.course_id, title=payload.title,
        session_date=payload.session_date, start_time=payload.start_time,
        end_time=payload.end_time, location_text=payload.location_text,
        location_id=payload.location_id, coordinator_id=payload.coordinator_id,
        notes=payload.notes, creator_id=_user_id(user))
    # ★ Round 6: "When the training session is created, if there are attachments
    # attached in the training course, transfer a copy over to the session."
    await materials.copy_course_to_session(db, delivery.course_id, delivery.id)
    return (await _with_counts(db, [delivery]))[0]


@router.get("/{delivery_id}", response_model=DeliveryOut)
async def get_session(delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    delivery = await crud.get_delivery(db, delivery_id)
    # ★ Widened outside, narrowed here. The outer gate is the one every employee
    # holds, so a coordinator of any role can reach their own session; the rule
    # about whose session it is lives here rather than in the dependency.
    await _require_session_access(db, user, delivery)
    return (await _with_counts(db, [delivery]))[0]


@router.patch("/{delivery_id}", response_model=DeliveryOut)
async def update_session(
    delivery_id: uuid.UUID, payload: DeliveryPatch, db: SessionDep, user: CanReportIncident,
):
    """Whoever is running it can change it.

    ★ Round 5, TC-07: "worker gets Insufficient permissions when trying to open
    a training session where they are the run by person on the training form."

    This endpoint was the one the round-4 work missed. Six endpoints were opened
    up to the nominated coordinator; this one kept `ehs.training.write`, and
    `CourseSessionPage` shows the instructor editor to whoever may run the
    session — so the coordinator could open the session, see the roster, mark
    it and close it, and got a flat refusal the moment they filled in the
    instructor. The sixth time in this module that a gate was widened on one
    side only, and the first five are all in the history of this file and its
    neighbours.

    Round 5 settled the scope: the person running it changes the instructor, the
    notes, the date and the place. Their session, their arrangements.

    ★ Reassigning it is not theirs. `coordinator_id` is HSE's nomination — the
    chain HSE specified is that HSE names a coordinator and the coordinator runs
    it — and handing a session to somebody else would let one ordinary employee
    give another edit rights over it. That stays with training-write, and it is
    the only field that does.
    """
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    # ★★ The *write* gate, not `_require_session_access`. That one admits anybody
    # holding `ehs.training.read` — which includes `auditor`, a role that exists
    # to read and nothing else, and `area_supervisor` for sessions that are not
    # theirs. Raised in review by uniops-41: widening this endpoint to the
    # coordinator had quietly widened it to every read-only role as well, which
    # is the same shape as the TC-19 holes and the reason that round's rule is
    # "pair every denial with an admission".
    await _require_roster_rights(db, user, delivery)
    fields = payload.model_dump(exclude_unset=True)
    # An end entered by a person replaces one inferred from the quiz.
    if "end_time" in fields:
        delivery.end_time_from_quiz = False
    if "coordinator_id" in fields:
        new_coordinator = fields.pop("coordinator_id")
        if not await has_permission(
                db, _user_id(user), user.get("role", ""), "ehs.training.write"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Who runs a session is HSE's to decide. You can change its "
                "details, not hand it to somebody else.")
        # Reassigning has to move the task as well, so it goes through crud
        # rather than being set like the rest.
        await crud.set_coordinator(db, delivery, new_coordinator)
    for key, value in fields.items():
        setattr(delivery, key, value)
    await db.flush()
    return (await _with_counts(db, [delivery]))[0]


@router.get("/{delivery_id}/roster", response_model=list[EnrolmentOut])
async def get_roster(delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_session_access(db, user, delivery)
    return await crud.roster(db, delivery_id)


@router.post("/{delivery_id}/roster", response_model=list[EnrolmentOut],
             status_code=status.HTTP_201_CREATED)
async def add_to_roster(
    delivery_id: uuid.UUID, payload: EnrolIn, db: SessionDep, user: CanReportIncident,
):
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    return await crud.enrol(db, delivery, payload.user_ids)


@router.delete("/{delivery_id}/roster/{enrolment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_roster(
    delivery_id: uuid.UUID, enrolment_id: uuid.UUID, db: SessionDep, user: CanReportIncident,
):
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    await crud.withdraw(db, delivery, enrolment_id)


class QuizMarkIn(BaseModel):
    passed: bool


@router.post("/{delivery_id}/roster/{enrolment_id}/quiz", response_model=EnrolmentOut)
async def mark_quiz(
    delivery_id: uuid.UUID, enrolment_id: uuid.UUID, payload: QuizMarkIn,
    db: SessionDep, user: CanReportIncident,
):
    """Mark the quiz passed by hand — a quiz done on paper. Round 6: "add a button
    that indicates that the quiz has been passed by the attendee." An online pass
    sets the same mark by itself."""
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    return await crud.set_quiz_passed(
        db, delivery, enrolment_id, payload.passed, marker_id=_user_id(user))


class QuizResultRow(BaseModel):
    user_id: uuid.UUID | None
    user_name: str
    score_percent: float
    passed: bool
    submitted_at: datetime


@router.get("/{delivery_id}/quiz-results", response_model=list[QuizResultRow])
async def quiz_results(delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """Every quiz attempt made at this session, newest first.

    Round 6: "place the quiz results also on the course session to allow the run
    by person easier visibility." Held to whoever may change the roster: the
    scores are other people's.
    """
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_roster_rights(db, user, delivery)
    rows = (await db.execute(
        select(QuizAttempt).where(QuizAttempt.delivery_id == delivery.id)
        .order_by(QuizAttempt.submitted_at.desc()))).scalars().all()
    return [QuizResultRow(user_id=a.user_id, user_name=a.user_name,
                          score_percent=float(a.score_percent), passed=a.passed,
                          submitted_at=a.submitted_at) for a in rows]


@router.post("/{delivery_id}/results", response_model=list[EnrolmentOut])
async def mark_results(
    delivery_id: uuid.UUID, payload: MarkIn, db: SessionDep, user: CanReportIncident,
):
    """Record who passed. Each pass writes a row into the training register.

    ★ The coordinator marks their own session. It was `ehs.training.write`, so
    "HSE fills in the instructor and marks pass or fail" was the only path — and
    the person who stood in the room watching them do it had to send the results
    to somebody else to type in.
    """
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    if delivery.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This session was cancelled")
    changed = await crud.mark_results(
        db, delivery, {r.enrolment_id: r.result for r in payload.results},
        marker_id=_user_id(user))
    if payload.complete:
        await crud.complete(db, delivery, absent_ok=payload.close_anyway)
    return changed


@router.post("/{delivery_id}/complete", response_model=DeliveryOut)
async def complete_session(
    delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident,
    close_anyway: bool = Query(default=False),
):
    delivery = await crud.get_delivery(db, delivery_id)
    # Closing a session is part of marking it, and marking it is the
    # coordinator's job.
    await _require_roster_rights(db, user, delivery)
    delivery = await crud.complete(db, delivery, absent_ok=close_anyway)
    return (await _with_counts(db, [delivery]))[0]


class ReopenIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/{delivery_id}/reopen", response_model=DeliveryOut)
async def reopen_session(
    delivery_id: uuid.UUID, payload: ReopenIn, db: SessionDep, user: CanWriteTraining,
):
    """HSE reopens a closed session to correct it, saying why (review of round
    10). The coordinator may then mark and close it again as before."""
    delivery = await crud.reopen(db, await crud.get_delivery(db, delivery_id),
                                 reason=payload.reason, user_id=_user_id(user))
    return (await _with_counts(db, [delivery]))[0]


@router.post("/{delivery_id}/cancel", response_model=DeliveryOut)
async def cancel_session(delivery_id: uuid.UUID, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    delivery = await crud.cancel(db, await crud.get_delivery(db, delivery_id))
    return (await _with_counts(db, [delivery]))[0]


# ── This session's material ─────────────────────────────────────────────────
# Round 6, TC-06: a session starts with a copy of its course's material, and
# holds files of any type but a page or a program, and links.

class SessionMaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    url: str | None = None
    uploaded_by_name: str | None
    created_at: datetime


class SessionLinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=255)


async def _material_on(db, delivery_id: uuid.UUID, material_id: uuid.UUID) -> CourseMaterial:
    row = await db.get(CourseMaterial, material_id)
    if row is None or row.delivery_id != delivery_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such material on this session")
    return row


@router.get("/{delivery_id}/materials", response_model=list[SessionMaterialOut])
async def list_session_materials(delivery_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_session_access(db, user, delivery)
    return list((await db.execute(
        select(CourseMaterial).where(CourseMaterial.delivery_id == delivery.id)
        .order_by(CourseMaterial.created_at))).scalars())


@router.post("/{delivery_id}/materials", response_model=SessionMaterialOut,
             status_code=status.HTTP_201_CREATED)
async def add_session_file(
    delivery_id: uuid.UUID, file: UploadFile, db: SessionDep, user: CanReportIncident,
    token: BearerToken,
):
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    uid = _user_id(user)
    return await materials.add_file(
        db, file, delivery_id=delivery.id, user_id=uid,
        user_name=await crud._name(db, uid), token=token)


@router.post("/{delivery_id}/materials/link", response_model=SessionMaterialOut,
             status_code=status.HTTP_201_CREATED)
async def add_session_link(
    delivery_id: uuid.UUID, payload: SessionLinkIn, db: SessionDep, user: CanReportIncident,
):
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    uid = _user_id(user)
    return await materials.add_link(
        db, payload.url, payload.title, delivery_id=delivery.id, user_id=uid,
        user_name=await crud._name(db, uid))


@router.get("/{delivery_id}/materials/{material_id}")
async def open_session_material(
    delivery_id: uuid.UUID, material_id: uuid.UUID, db: SessionDep, user: CanReportIncident,
    token: BearerToken,
):
    delivery = await crud.get_delivery(db, delivery_id)
    await _require_session_access(db, user, delivery)
    row = await _material_on(db, delivery.id, material_id)
    if row.file_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{row.filename} is a link: {row.url}")
    return await proxy_download(row.file_id, token)


@router.delete("/{delivery_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_session_material(
    delivery_id: uuid.UUID, material_id: uuid.UUID, db: SessionDep, user: CanReportIncident,
    token: BearerToken,
):
    delivery = await crud.get_delivery(db, delivery_id)
    crud.assert_not_closed(delivery)
    await _require_roster_rights(db, user, delivery)
    # ★ The course and other sessions may hold the same file.
    await materials.remove(db, await _material_on(db, delivery.id, material_id), token)
