"""The Joint Health and Safety Committee."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import CurrentUserPayload, SessionDep
from app.core.permissions import CanManageJhsc, CanReadJhsc
from app.crud import form as forms_crud
from app.crud import jhsc as crud
from app.models.config import EhsConfig
from app.models.form import FormTemplate
from app.models.mirrors import User
from app.services import branding, mail, notify
from app.services import minutes as minutes_service
from app.services.http_headers import content_disposition

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


class MinutesRowOut(BaseModel):
    label: str
    text: str | None
    heading: bool = False
    emphasis: bool = False


@router.get("/meetings/{meeting_id}/minutes-answers", response_model=list[MinutesRowOut])
async def minutes_answers(meeting_id: uuid.UUID, db: SessionDep, user: CanReadJhsc):  # noqa: ARG001
    """The minutes form's questions and answers, for showing on the meeting.

    Round 6, TC-04: "a saved meeting minutes form does not get saved on the
    meeting." It was attached; nothing read it back.
    """
    meeting = await crud.get_meeting(db, meeting_id)
    return [MinutesRowOut(label=r.label, text=r.text, heading=r.heading, emphasis=r.emphasis)
            for r in await minutes_service.rows_for(db, meeting.minutes_submission_id)]


@router.get("/meetings/{meeting_id}/print")
async def print_minutes(meeting_id: uuid.UUID, db: SessionDep, user: CanReadJhsc):
    """The minutes as one document: attendance, agenda, minutes, recommendations.

    Round 6, TC-04: "there is no means to print the meeting minutes including
    the attendance, agenda, minutes, and recommendations."
    """
    from app.models.statutory import StatutoryDeadline
    meeting = await crud.get_meeting(db, meeting_id)
    printer = await db.get(User, _user_id(user))
    recs = await crud.list_recommendations(db, meeting_id=meeting.id, outstanding_only=False)
    reply_due = {d.source_id: d for d in (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "jhsc_rec",
            StatutoryDeadline.source_id.in_([r.id for r in recs]),
        ))).scalars()} if recs else {}
    pdf = minutes_service.render(
        meeting=meeting,
        attendance=await crud.attendance_for(db, meeting.id),
        recommendations=recs,
        rows=await minutes_service.rows_for(db, meeting.minutes_submission_id),
        printed_by=printer.full_name if printer else None,
        reply_due=reply_due,
        logo_data_url=await branding.logo_data_url(db),
    )
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": content_disposition(f"minutes-{meeting.meeting_no}")})


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
    # Round 8: where it happened. Optional — not every recommendation is about a place.
    location_id: uuid.UUID | None = None
    # ★ Recorded and sent in one step, which is what the committee asked for:
    # naming who has to answer was a second screen they had to remember to go
    # back to, and an unsent recommendation is one nobody owes a reply to.
    # Left empty it still only records — the twenty-one days run from the
    # recording either way, so recording first and sending later stays valid.
    send_to: list[uuid.UUID] = Field(default_factory=list)
    # The body as approved in the preview. Empty means send the standard text.
    message: str | None = None


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
    location_id: uuid.UUID | None = None
    location_path: str | None = None
    status_item_id: uuid.UUID | None = None
    status_label: str | None = None
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
    # Round 12, the JHSC workflow: first opened by whoever it was sent to, and
    # verified by HSE in the register.
    reviewed_at: datetime | None = None
    reviewed_by_name: str | None = None
    hse_verified_at: datetime | None = None
    hse_verified_by_name: str | None = None
    # Round 9: when the reply is due, so the list can say which were answered
    # on time and which late.
    reply_due_at: datetime | None = None
    # Round 10, TC-10: the state of the corrective action that answered it,
    # for the CAPA figures on the recommendations tab.
    action_status: str | None = None


@router.get("/recommendations", response_model=list[RecommendationOut])
async def list_recommendations(
    db: SessionDep, user: CanReadJhsc,  # noqa: ARG001
    meeting_id: uuid.UUID | None = None,
    outstanding: bool = Query(default=False, description="Only ones still owed a reply"),
):
    from app.models.statutory import StatutoryDeadline
    rows = await crud.list_recommendations(
        db, meeting_id=meeting_id, outstanding_only=outstanding)
    due = dict((await db.execute(select(
        StatutoryDeadline.source_id, StatutoryDeadline.due_at).where(
        StatutoryDeadline.kind == "jhsc_reply_21d",
        StatutoryDeadline.source_id.in_([r.id for r in rows])))).all()) if rows else {}
    from app.models.action import Action
    action_ids = [r.action_id for r in rows if r.action_id]
    states = dict((await db.execute(select(Action.id, Action.status).where(
        Action.id.in_(action_ids)))).all()) if action_ids else {}
    out = []
    for r in rows:
        item = RecommendationOut.model_validate(r)
        item.reply_due_at = due.get(r.id)
        item.action_status = states.get(r.action_id) if r.action_id else None
        out.append(item)
    return out


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
    # ★ Both directions, in the one place the link is made. Without the second
    # one the form cannot say what it is for, and an empty set of minutes looks
    # exactly like a new blank form.
    submission.source_type = "jhsc"
    submission.source_id = meeting.id
    submission.source_ref = meeting.meeting_no
    await db.flush()
    return MinutesFormStarted(submission_id=submission.id,
                              submission_no=submission.submission_no)


@router.post("/meetings/{meeting_id}/recommendations", response_model=RecommendationOut,
             status_code=status.HTTP_201_CREATED)
async def add_recommendation(
    meeting_id: uuid.UUID, payload: RecommendationIn, db: SessionDep, user: CanManageJhsc,
):
    """Recording one starts OHSA s.9(20)'s twenty-one days.

    Sending is part of the same request when recipients are named. It is not a
    separate clock: the twenty-one days run from the recording, so a
    recommendation recorded now and sent tomorrow is owed a reply on the same
    date either way.
    """
    meeting = await crud.get_meeting(db, meeting_id)
    rec = await crud.add_recommendation(
        db, meeting, text=payload.recommendation, raised_by_name=payload.raised_by_name,
        location_id=payload.location_id)
    if payload.send_to:
        rec = await crud.send_recommendation(
            db, rec, user_ids=payload.send_to, sender_id=_user_id(user),
            message=payload.message)
    return rec


class SendRecommendationIn(BaseModel):
    """Who the recommendation is being put in front of."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[uuid.UUID] = Field(min_length=1)
    # What the sender approved in the preview, when they changed it.
    message: str | None = None


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
        db, rec, user_ids=payload.user_ids, sender_id=_user_id(user),
        message=payload.message)


class PreviewIn(BaseModel):
    """What the notice would say, before anybody is told anything."""

    model_config = ConfigDict(extra="forbid")

    meeting_id: uuid.UUID
    recommendation: str = Field(min_length=1)
    # ★ Set when previewing a recommendation that already exists, which is what
    # "record who this was sent to" does. Without it the preview numbered every
    # message as the next one to be recorded — so re-sending #1 previewed as #3
    # and sent as #1. The send was right and the preview was not, which is the
    # one thing this screen exists to prevent. Found in review by uniops-9a.
    recommendation_id: uuid.UUID | None = None
    # Where it happened, for one not yet recorded; an existing one has its own.
    location_id: uuid.UUID | None = None
    user_ids: list[uuid.UUID] = Field(default_factory=list)
    # A draft the sender has already edited, so a second look shows their words.
    message: str | None = None


class PreviewRecipient(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    # ★ Whether this person will actually receive it. `mail.recipients` drops
    # anybody inactive or opted out of email, silently — which is right for a
    # second channel and wrong for a preview whose whole job is to say what is
    # about to happen. The common case is an opt-out, not a missing address:
    # `users.email` is NOT NULL, and anybody sharing a mailbox is set to `none`
    # on purpose.
    will_receive: bool
    reason: str | None = None


class PreviewOut(BaseModel):
    subject: str
    body: str
    recipients: list[PreviewRecipient]
    # Named here as well as per-recipient, because this is the line somebody
    # reads before pressing send.
    unreachable: list[str]
    seq_will_be: int


@router.post("/recommendations/preview", response_model=PreviewOut)
async def preview_recommendation(
    payload: PreviewIn, db: SessionDep, user: CanManageJhsc,  # noqa: ARG001
):
    """The message, in full, before it is sent — and who will not get it.

    ★ The committee asked to review the communication in draft and to be able
    to change it before sending. The subject and body come from the same
    function the send uses, so what is shown here cannot drift from what goes
    out. Nothing is written by this request.
    """
    meeting = await crud.get_meeting(db, payload.meeting_id)

    if payload.recommendation_id is not None:
        # An existing one: its own number, and its own wording as recorded.
        existing = await crud.get_recommendation(db, payload.recommendation_id)
        # ★ And it has to belong to the meeting named, or the preview carries one
        # meeting's heading over another meeting's recommendation. Only reachable
        # by a caller passing the wrong pair, which is exactly when a preview
        # must not quietly agree. Raised in review by uniops-9a.
        if existing.meeting_id != meeting.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "That recommendation belongs to a different meeting.")
        seq = existing.seq
        wording = payload.recommendation or existing.recommendation
        where = existing.location_path
    else:
        seq = await crud.next_recommendation_seq(db, meeting.id)
        wording = payload.recommendation
        where = await crud.location_path_of(db, payload.location_id)

    # A stand-in with the fields the message needs, so that no half-built row
    # reaches the database on the way to a preview.
    draft = SimpleNamespace(seq=seq, recommendation=wording, location_path=where)
    subject, default_body = notify.jhsc_recommendation_message(meeting, draft)

    people = list((await db.execute(
        select(User).where(User.id.in_(payload.user_ids)))).scalars().all()) \
        if payload.user_ids else []
    addressable = {u.id for u in await mail.recipients(db, [p.id for p in people])}

    rows: list[PreviewRecipient] = []
    for person in people:
        why = None
        if person.id not in addressable:
            if not person.is_active:
                why = "no longer active"
            elif not person.email:
                why = "no email address on file"
            else:
                why = "has opted out of email"
        rows.append(PreviewRecipient(
            id=person.id, name=person.full_name, email=person.email,
            will_receive=person.id in addressable, reason=why))

    return PreviewOut(
        subject=subject,
        body=payload.message if payload.message and payload.message.strip() else default_body,
        recipients=rows,
        unreachable=[r.name for r in rows if not r.will_receive],
        seq_will_be=seq,
    )


@router.post("/recommendations/{rec_id}/respond", response_model=RecommendationOut)
async def respond(
    rec_id: uuid.UUID, payload: ResponseIn, db: SessionDep, user: CanManageJhsc,
):
    rec = await crud.get_recommendation(db, rec_id)
    return await crud.respond(
        db, rec, response=payload.response, outcome=payload.outcome,
        user_id=_user_id(user), action_id=payload.action_id)


# ── One recommendation, and answering it (round 9) ───────────────────────────
# "when a worker or a supervisor opens a JHSC recommendation assigned to them
# from the my actions register, open the recommendation directly", and "when a
# worker … has a jhsc recommendation assigned to them, they can not open the
# recommendation and fill in what was completed. give them a root cause
# corrective action form … once it is submitted, remove it from the workers my
# actions registry. allow the HSE role edit rights after this is submitted."
#
# Whoever it was sent to may open it and answer it, without the committee's own
# keys: being asked is the permission. Everybody else needs ehs.jhsc.read to see
# it and ehs.jhsc.manage to answer or correct it.


class ActionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_no: str
    title: str
    description: str | None
    owner_name: str | None
    due_date: date
    priority: str
    status: str


class RecommendationDetailOut(RecommendationOut):
    meeting_no: str
    held_on: date | None = None
    reply_due_at: datetime | None = None
    may_answer: bool = False
    may_manage: bool = False
    # Round 10: HSE, and whoever it was sent to, move it along the list.
    may_set_status: bool = False
    may_hse_verify: bool = False
    may_complete: bool = False
    action: ActionBrief | None = None


class AnswerIn(BaseModel):
    """The corrective action form, as on an incident."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner_id: uuid.UUID
    due_date: date
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")
    hierarchy_of_control: str | None = Field(
        default=None, pattern="^(elimination|substitution|engineering|administrative|ppe)$")


class ResponsePatch(BaseModel):
    response: str | None = Field(default=None, min_length=1)
    outcome: str | None = Field(default=None, pattern="^(accepted|declined|deferred)$")


def _was_sent_to(rec, user_id: uuid.UUID) -> bool:
    return any(str(p.get("id")) == str(user_id) for p in (rec.sent_to or []))


async def _can(db, user: dict, key: str) -> bool:
    from uniops_authz import has_permission
    return await has_permission(db, _user_id(user), user.get("role", ""), key)


async def _detail(db, rec, user: dict) -> RecommendationDetailOut:
    from app.models.action import Action
    from app.models.statutory import StatutoryDeadline
    meeting = await crud.get_meeting(db, rec.meeting_id)
    due = (await db.execute(select(StatutoryDeadline.due_at).where(
        StatutoryDeadline.source_id == rec.id,
        StatutoryDeadline.kind == "jhsc_reply_21d"))).scalars().first()
    action = await db.get(Action, rec.action_id) if rec.action_id else None
    manage = await _can(db, user, "ehs.jhsc.manage")
    out = RecommendationDetailOut.model_validate({
        **RecommendationOut.model_validate(rec).model_dump(),
        "meeting_no": meeting.meeting_no, "held_on": meeting.held_on,
        "reply_due_at": due,
        "may_answer": rec.responded_at is None and (manage or _was_sent_to(rec, _user_id(user))),
        "may_manage": manage,
        "may_set_status": manage or _was_sent_to(rec, _user_id(user)),
        # Step 11: answered, its action done, not yet verified — and the caller HSE.
        # ★ Re-review N1: Completed is offered only to whoever may verify.
        "may_complete": await _can(db, user, "ehs.incident.close"),
        "may_hse_verify": (rec.responded_at is not None
                           and (action is None or action.status in ("closed", "cancelled"))
                           and not await crud.is_completed(db, rec)
                           and await _can(db, user, "ehs.incident.close")),
        "action": ActionBrief.model_validate(action) if action else None,
    })
    return out


@router.get("/recommendations/{rec_id}", response_model=RecommendationDetailOut)
async def get_recommendation(rec_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    rec = await crud.get_recommendation(db, rec_id)
    if not (_was_sent_to(rec, _user_id(user)) or await _can(db, user, "ehs.jhsc.read")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This recommendation was not sent to you")
    # Round 12, step 4: "When worker opens the request to read what is
    # required, the status should automatically be changed to Under Review."
    await crud.opened(db, rec, user_id=_user_id(user))
    return await _detail(db, rec, user)


@router.post("/recommendations/{rec_id}/verify", response_model=RecommendationDetailOut)
async def hse_verify_recommendation(rec_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """Round 12, step 11: "HSE role verifies the recommendations using the JHSC
    registry." Held to the key only HSE holds (ehs.incident.close)."""
    if not await _can(db, user, "ehs.incident.close"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "HSE verifies a recommendation")
    rec = await crud.get_recommendation(db, rec_id)
    await crud.hse_verify(db, rec, user_id=_user_id(user))
    return await _detail(db, rec, user)


class RecHistoryEntry(BaseModel):
    at: datetime
    what: str
    by: str | None = None
    text: str | None = None


@router.get("/recommendations/{rec_id}/history", response_model=list[RecHistoryEntry])
async def recommendation_history(rec_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """Round 12: "the history the recommendation has gone through should be
    listed for audit and tracking purposes. when it was created, the reply,
    the capa creation through to completion steps, and when it was completed."
    The recommendation's own steps; its corrective action's steps are
    /actions/history, shown beside it."""
    rec = await crud.get_recommendation(db, rec_id)
    if not (_was_sent_to(rec, _user_id(user)) or await _can(db, user, "ehs.jhsc.read")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This recommendation was not sent to you")
    from app.models.action import Action
    out = [RecHistoryEntry(at=rec.created_at, what="Recommended", by=rec.raised_by_name,
                           text=rec.location_path)]
    if rec.sent_at:
        out.append(RecHistoryEntry(
            at=rec.sent_at, what="Assigned", by=rec.sent_by_name,
            text="To " + (", ".join(p.get("name") or "?" for p in rec.sent_to or []) or "nobody")))
    if rec.reviewed_at:
        out.append(RecHistoryEntry(at=rec.reviewed_at, what="Opened — under review",
                                   by=rec.reviewed_by_name))
    if rec.responded_at:
        out.append(RecHistoryEntry(at=rec.responded_at, what=f"Replied ({rec.outcome})",
                                   by=rec.responded_by_name, text=rec.response))
    action = await db.get(Action, rec.action_id) if rec.action_id else None
    if action is not None:
        out.append(RecHistoryEntry(at=action.created_at, what=f"Corrective action {action.action_no} raised",
                                   by=None, text=f"For {action.owner_name}"))
        if action.closed_at:
            out.append(RecHistoryEntry(at=action.closed_at,
                                       what=f"Corrective action {action.action_no} verified and closed"))
    if rec.hse_verified_at:
        out.append(RecHistoryEntry(at=rec.hse_verified_at, what="Verified by HSE — completed",
                                   by=rec.hse_verified_by_name))
    out.sort(key=lambda e: e.at)
    return out


@router.post("/recommendations/{rec_id}/answer", response_model=RecommendationDetailOut)
async def answer_recommendation(
    rec_id: uuid.UUID, payload: AnswerIn, db: SessionDep, user: CurrentUserPayload,
):
    """The corrective action that answers it. Raises the action (in its owner's
    My Actions), records the reply — which stops the twenty-one days and closes
    every recipient's task — and tells the JHSC list under Who is told."""
    from app.crud import action as action_crud
    from app.schemas.action import ActionCreate
    rec = await crud.get_recommendation(db, rec_id)
    uid = _user_id(user)
    if not (_was_sent_to(rec, uid) or await _can(db, user, "ehs.jhsc.manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the people it was sent to, or HSE, can answer it")
    # ★ Review (uniops-8b): two recipients submitting together both passed
    # the check below — two corrective actions, the deadline met twice, two
    # emails. Locked first, so the second waits for the first and is refused.
    rec = await crud.get_recommendation(db, rec_id, for_update=True)
    if rec.responded_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This recommendation has already been answered")
    meeting = await crud.get_meeting(db, rec.meeting_id)
    action = await action_crud.create(db, ActionCreate(
        source_type="jhsc", source_id=meeting.id, title=payload.title,
        description=payload.description, owner_id=payload.owner_id,
        due_date=payload.due_date, priority=payload.priority,
        hierarchy_of_control=payload.hierarchy_of_control),
        source_ref=f"{meeting.meeting_no} #{rec.seq}")
    response = payload.title + (f"\n\n{payload.description}" if payload.description else "")
    rec = await crud.respond(db, rec, response=response, outcome="accepted",
                             user_id=uid, action_id=action.id)
    await notify.jhsc_recommendation_answered(db, meeting, rec, action)
    return await _detail(db, rec, user)


@router.patch("/recommendations/{rec_id}/response", response_model=RecommendationDetailOut)
async def correct_response(
    rec_id: uuid.UUID, payload: ResponsePatch, db: SessionDep, user: CanManageJhsc,
):
    """HSE correcting an answer after it is in. Every change is in the record's
    change history; the corrective action itself is edited on its own page."""
    rec = await crud.get_recommendation(db, rec_id)
    if rec.responded_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "It has not been answered yet; answer it instead")
    if payload.response is not None:
        rec.response = payload.response
    if payload.outcome is not None:
        rec.outcome = payload.outcome
    await db.flush()
    return await _detail(db, rec, user)


class StatusIn(BaseModel):
    item_id: uuid.UUID


@router.put("/recommendations/{rec_id}/status", response_model=RecommendationDetailOut)
async def set_recommendation_status(
    rec_id: uuid.UUID, payload: StatusIn, db: SessionDep, user: CurrentUserPayload,
):
    """Round 10, TC-11: where the work stands — "new request", "reviewed by
    maintenance" … from the list HSE keeps under Settings → Lists."""
    rec = await crud.get_recommendation(db, rec_id)
    if not (_was_sent_to(rec, _user_id(user)) or await _can(db, user, "ehs.jhsc.manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the people it was sent to, or HSE, can change its state")
    await crud.set_status(db, rec, payload.item_id, user_id=_user_id(user),
                          may_verify=await _can(db, user, "ehs.incident.close"))
    return await _detail(db, rec, user)
