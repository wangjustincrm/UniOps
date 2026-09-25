"""The Joint Health and Safety Committee."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageJhsc, CanReadJhsc
from app.crud import form as forms_crud
from app.crud import jhsc as crud
from app.models.config import EhsConfig
from app.models.form import FormTemplate

router = APIRouter(prefix="/jhsc")


# ── Roster ───────────────────────────────────────────────────────────────────

class MemberIn(BaseModel):
    user_id: uuid.UUID
    representation: str = Field(default="worker", pattern="^(worker|management)$")
    committee_role: str = Field(default="member", pattern="^(member|co_chair|secretary)$")
    is_certified: bool = False
    certified_on: date | None = None
    term_start: date | None = None
    term_end: date | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    department_name: str | None
    representation: str
    committee_role: str
    is_certified: bool
    certified_on: date | None
    term_start: date | None
    term_end: date | None
    is_active: bool


class CompositionOut(BaseModel):
    total: int
    worker_members: int
    management_members: int
    certified_total: int
    certified_worker: int
    certified_management: int
    worker_majority_met: bool
    certified_both_sides_met: bool


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    db: SessionDep, user: CanReadJhsc,  # noqa: ARG001
    include_retired: bool = False,
):
    return await crud.list_members(db, active_only=not include_retired)


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(payload: MemberIn, db: SessionDep, user: CanManageJhsc):  # noqa: ARG001
    return await crud.add_member(db, payload.model_dump())


@router.patch("/members/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: uuid.UUID, payload: MemberIn, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    member = await crud.get_member(db, member_id)
    for key, value in payload.model_dump(exclude={"user_id"}, exclude_unset=True).items():
        setattr(member, key, value)
    await db.flush()
    return member


@router.post("/members/{member_id}/retire", response_model=MemberOut)
async def retire_member(member_id: uuid.UUID, db: SessionDep, user: CanManageJhsc):  # noqa: ARG001
    return await crud.retire_member(db, await crud.get_member(db, member_id))


@router.get("/composition", response_model=CompositionOut)
async def composition(db: SessionDep, user: CanReadJhsc):  # noqa: ARG001
    """Whether the committee is lawfully constituted, per OHSA s.9."""
    return await crud.composition(db)


# ── Meetings ─────────────────────────────────────────────────────────────────

class MeetingIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    held_on: date
    started_at: datetime | None = None
    ended_at: datetime | None = None
    location_text: str | None = Field(default=None, max_length=200)
    agenda: str | None = None
    # The department this month's post-meeting inspection covers.
    inspection_department_id: uuid.UUID | None = None


class MeetingPatch(BaseModel):
    title: str | None = None
    held_on: date | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    location_text: str | None = None
    agenda: str | None = None
    minutes: str | None = None
    status: str | None = Field(default=None, pattern="^(scheduled|held)$")


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_no: str
    title: str | None
    held_on: date
    started_at: datetime | None
    ended_at: datetime | None
    location_text: str | None
    agenda: str | None
    minutes: str | None
    # The minutes as a filled-in form, where a template is configured. Older
    # meetings carry the free-text above and nothing here.
    minutes_submission_id: uuid.UUID | None
    inspection_department_name: str | None
    status: str
    published_at: datetime | None


class AttendanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    representation: str | None
    attendance: str


class AttendanceMark(BaseModel):
    id: uuid.UUID
    attendance: str = Field(pattern="^(present|absent|regrets)$")


class AttendanceIn(BaseModel):
    marks: list[AttendanceMark] = Field(min_length=1)


@router.get("/meetings", response_model=list[MeetingOut])
async def list_meetings(db: SessionDep, user: CanReadJhsc):  # noqa: ARG001
    return await crud.list_meetings(db)


@router.post("/meetings", response_model=MeetingOut, status_code=status.HTTP_201_CREATED)
async def create_meeting(payload: MeetingIn, db: SessionDep, user: CanManageJhsc):
    return await crud.create_meeting(db, payload.model_dump(), creator_id=_user_id(user))


@router.get("/meetings/{meeting_id}", response_model=MeetingOut)
async def get_meeting(meeting_id: uuid.UUID, db: SessionDep, user: CanReadJhsc):  # noqa: ARG001
    return await crud.get_meeting(db, meeting_id)


@router.patch("/meetings/{meeting_id}", response_model=MeetingOut)
async def update_meeting(
    meeting_id: uuid.UUID, payload: MeetingPatch, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    meeting = await crud.get_meeting(db, meeting_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(meeting, key, value)
    await db.flush()
    return meeting


@router.get("/meetings/{meeting_id}/attendance", response_model=list[AttendanceOut])
async def get_attendance(meeting_id: uuid.UUID, db: SessionDep, user: CanReadJhsc):  # noqa: ARG001
    await crud.get_meeting(db, meeting_id)
    return await crud.attendance_for(db, meeting_id)


@router.post("/meetings/{meeting_id}/attendance", response_model=list[AttendanceOut])
async def set_attendance(
    meeting_id: uuid.UUID, payload: AttendanceIn, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    meeting = await crud.get_meeting(db, meeting_id)
    return await crud.set_attendance(
        db, meeting, {m.id: m.attendance for m in payload.marks})


@router.post("/meetings/{meeting_id}/publish", response_model=MeetingOut)
async def publish_meeting(meeting_id: uuid.UUID, db: SessionDep, user: CanManageJhsc):
    """Minutes to management, to every member, and onto the noticeboard."""
    meeting = await crud.get_meeting(db, meeting_id)
    return await crud.publish_meeting(db, meeting, user_id=_user_id(user))


# ── Recommendations ──────────────────────────────────────────────────────────

class RecommendationIn(BaseModel):
    recommendation: str = Field(min_length=1)
    raised_by_name: str | None = Field(default=None, max_length=255)


class ResponseIn(BaseModel):
    response: str = Field(min_length=1)
    outcome: str = Field(pattern="^(accepted|declined|deferred)$")
    # The corrective action the reply commits to, when it commits to one.
    action_id: uuid.UUID | None = None


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    meeting_id: uuid.UUID
    seq: int
    recommendation: str
    raised_by_name: str | None
    response: str | None
    outcome: str | None
    # Who it was put in front of, and when. Without it the committee could see
    # that nobody had answered and not who had failed to.
    sent_at: datetime | None
    sent_by_name: str | None
    sent_to: list
    responded_at: datetime | None
    responded_by_name: str | None
    action_id: uuid.UUID | None
    created_at: datetime


@router.get("/recommendations", response_model=list[RecommendationOut])
async def list_recommendations(
    db: SessionDep, user: CanReadJhsc,  # noqa: ARG001
    meeting_id: uuid.UUID | None = None,
    outstanding: bool = Query(default=False, description="Only ones still owed a reply"),
):
    return await crud.list_recommendations(
        db, meeting_id=meeting_id, outstanding_only=outstanding)


class MinutesFormStarted(BaseModel):
    """Where the minutes now live, so the screen can open them."""

    submission_id: uuid.UUID
    submission_no: str


class MinutesFormIn(BaseModel):
    """Attaching the filled-in minutes form to the meeting."""

    model_config = ConfigDict(extra="forbid")

    submission_id: uuid.UUID


@router.post("/meetings/{meeting_id}/minutes-form", response_model=MeetingOut)
async def attach_minutes_form(
    meeting_id: uuid.UUID, payload: MinutesFormIn, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    """The minutes, written on whichever form Settings says.

    HSE asked for the format to be configurable "to allow for configuration and
    readability changes that happen over time" — so it is a form template
    rather than a second layout engine beside the one that exists.

    The pointer is replaced, not appended: one meeting has one set of minutes,
    and a second submission means the first was wrong.
    """
    meeting = await crud.get_meeting(db, meeting_id)
    submission = await forms_crud.get_submission(db, payload.submission_id)
    meeting.minutes_submission_id = submission.id
    await db.flush()
    return MeetingOut.model_validate(meeting)


@router.post("/meetings/{meeting_id}/minutes-form/start", response_model=MinutesFormStarted,
             status_code=status.HTTP_201_CREATED)
async def start_minutes_form(
    meeting_id: uuid.UUID, db: SessionDep, user: CanManageJhsc,
):
    """Begin the minutes for this meeting, on the configured form.

    ★ One call because the two halves have to stay together. The screen told
    people to fill the form in under Forms and attach it here, and there was
    nothing here to attach it with — the endpoint existed and nothing called
    it, so the minutes and the meeting they belong to were two unrelated
    records. Starting it from the meeting makes the link at the moment the
    document comes into existence, which is the only moment nobody can forget.
    """
    meeting = await crud.get_meeting(db, meeting_id)

    # ★ Idempotent. Pressed twice — by one person on a slow connection, or by
    # two members of the committee — a second call would point the meeting at a
    # fresh empty form and cut the half-written minutes loose in Forms, with
    # nothing saying where they went. These are the committee's statutory
    # record; the second press opens the one that exists.
    if meeting.minutes_submission_id is not None:
        existing = await forms_crud.get_submission(db, meeting.minutes_submission_id)
        return MinutesFormStarted(submission_id=existing.id,
                                  submission_no=existing.submission_no)

    config = (await db.execute(select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()
    code = (config.jhsc_minutes_template_code if config else None) or None
    if not code:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No minutes form has been chosen yet. Pick one under Settings → Rules.")

    template = (await db.execute(
        select(FormTemplate).where(FormTemplate.code == code))).scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Settings names '{code}' as the minutes form, and there is no such "
            "form. Choose one that exists under Settings → Rules.")

    submission = await forms_crud.start_submission(
        db, template.id, user_id=_user_id(user),
        location_id=None, occurred_at=None)
    meeting.minutes_submission_id = submission.id
    await db.flush()
    return MinutesFormStarted(submission_id=submission.id,
                              submission_no=submission.submission_no)


@router.post("/meetings/{meeting_id}/recommendations", response_model=RecommendationOut,
             status_code=status.HTTP_201_CREATED)
async def add_recommendation(
    meeting_id: uuid.UUID, payload: RecommendationIn, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    """Recording one starts OHSA s.9(20)'s twenty-one days."""
    meeting = await crud.get_meeting(db, meeting_id)
    return await crud.add_recommendation(
        db, meeting, text=payload.recommendation, raised_by_name=payload.raised_by_name)


class SendRecommendationIn(BaseModel):
    """Who the recommendation is being put in front of."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[uuid.UUID] = Field(min_length=1)


@router.post("/recommendations/{rec_id}/send", response_model=RecommendationOut)
async def send_recommendation(
    rec_id: uuid.UUID, payload: SendRecommendationIn, db: SessionDep, user: CanManageJhsc,
):
    """Record who it went to, and tell them.

    ★ This does not start the twenty-one day clock. That runs from when the
    recommendation was recorded — if sending started it, not sending would
    extend a statutory deadline indefinitely, which is the one thing this
    module must never make possible.
    """
    rec = await crud.get_recommendation(db, rec_id)
    return await crud.send_recommendation(
        db, rec, user_ids=payload.user_ids, sender_id=_user_id(user))


@router.post("/recommendations/{rec_id}/respond", response_model=RecommendationOut)
async def respond(
    rec_id: uuid.UUID, payload: ResponseIn, db: SessionDep, user: CanManageJhsc,
):
    rec = await crud.get_recommendation(db, rec_id)
    return await crud.respond(
        db, rec, response=payload.response, outcome=payload.outcome,
        user_id=_user_id(user), action_id=payload.action_id)
