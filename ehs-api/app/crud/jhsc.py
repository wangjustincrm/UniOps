"""The committee: who is on it, what it met about, and what it recommended.

The one calculation worth reading carefully is `composition`. OHSA s.9 requires
at least half the committee to be workers who do not exercise managerial
functions, and requires certified members from both sides. Those are not
reporting lines — they are the conditions under which the committee is lawfully
constituted, and the roster page states them rather than leaving somebody to
count names.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.form import FormSubmission
from app.models.jhsc import JhscAttendance, JhscMeeting, JhscMember, JhscRecommendation
from app.models.mirrors import Department, Location, User
from app.services import deadlines as deadline_service
from app.services import notify
from app.services import tasks as task_service
from app.services.numbering import next_number
from app.services.day_range import plant_today

DOC_JHSC = task_service.DOC_JHSC


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


# ── Roster ───────────────────────────────────────────────────────────────────

async def list_members(db: AsyncSession, *, active_only: bool = True) -> list[JhscMember]:
    stmt = select(JhscMember)
    if active_only:
        stmt = stmt.where(JhscMember.is_active.is_(True))
    return list((await db.execute(
        stmt.order_by(JhscMember.representation, JhscMember.user_name))).scalars())


async def get_member(db: AsyncSession, member_id: uuid.UUID) -> JhscMember:
    member = await db.get(JhscMember, member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on the committee")
    return member


async def add_member(db: AsyncSession, payload: dict) -> JhscMember:
    user = await db.get(User, payload["user_id"])
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")

    seated = (await db.execute(select(JhscMember).where(
        JhscMember.user_id == user.id, JhscMember.is_active.is_(True)))).scalar_one_or_none()
    if seated is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{user.full_name} already holds a seat")

    dept = (await db.execute(
        select(Department.name).where(Department.id == user.department_id))).scalar()
    member = JhscMember(
        id=uuid.uuid4(), user_id=user.id, user_name=user.full_name, department_name=dept,
        representation=payload.get("representation", "worker"),
        committee_role=payload.get("committee_role", "member"),
        is_certified=payload.get("is_certified", False),
        certified_on=payload.get("certified_on"),
        term_start=payload.get("term_start"), term_end=payload.get("term_end"),
    )
    db.add(member)
    await db.flush()
    return member


async def retire_member(db: AsyncSession, member: JhscMember, *, on: date | None = None) -> JhscMember:
    """Stand somebody down without deleting them.

    Deleting would take their attendance history with them, and last year's
    quorum has to stay checkable.
    """
    member.is_active = False
    member.term_end = on or plant_today()
    await db.flush()
    return member


async def composition(db: AsyncSession) -> dict:
    """Whether the committee is lawfully made up, not just how big it is."""
    members = await list_members(db, active_only=True)
    workers = [m for m in members if m.representation == "worker"]
    management = [m for m in members if m.representation == "management"]
    return {
        "total": len(members),
        "worker_members": len(workers),
        "management_members": len(management),
        "certified_total": sum(1 for m in members if m.is_certified),
        "certified_worker": sum(1 for m in workers if m.is_certified),
        "certified_management": sum(1 for m in management if m.is_certified),
        # s.9(6): at least half must be workers who do not exercise managerial
        # functions. Stated as a boolean because that is how it is asked.
        "worker_majority_met": len(members) > 0 and len(workers) * 2 >= len(members),
        # s.9(12): one certified member from each side.
        "certified_both_sides_met": (
            any(m.is_certified for m in workers) and any(m.is_certified for m in management)
        ),
    }


# ── Meetings ─────────────────────────────────────────────────────────────────

async def list_meetings(db: AsyncSession, *, limit: int = 24) -> list[JhscMeeting]:
    return list((await db.execute(
        select(JhscMeeting).order_by(JhscMeeting.held_on.desc()).limit(limit))).scalars())


async def get_meeting(db: AsyncSession, meeting_id: uuid.UUID) -> JhscMeeting:
    meeting = await db.get(JhscMeeting, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found")
    return meeting


async def create_meeting(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> JhscMeeting:
    dept_id = payload.get("inspection_department_id")
    meeting = JhscMeeting(
        id=uuid.uuid4(),
        meeting_no=await next_number(db, "jhsc_meeting"),
        title=payload.get("title"),
        held_on=payload["held_on"],
        started_at=payload.get("started_at"),
        ended_at=payload.get("ended_at"),
        location_text=payload.get("location_text"),
        agenda=payload.get("agenda"),
        inspection_department_id=dept_id,
        inspection_department_name=(await db.execute(
            select(Department.name).where(Department.id == dept_id))).scalar() if dept_id else None,
        created_by=creator_id,
    )
    db.add(meeting)
    await db.flush()
    # Seed the attendance sheet from the current roster, so recording who came
    # is ticking a list rather than typing twenty-three names.
    for member in await list_members(db, active_only=True):
        db.add(JhscAttendance(
            id=uuid.uuid4(), meeting_id=meeting.id, member_id=member.id,
            user_id=member.user_id, user_name=member.user_name,
            representation=member.representation, attendance="absent",
        ))
    await db.flush()
    return meeting


async def attendance_for(db: AsyncSession, meeting_id: uuid.UUID) -> list[JhscAttendance]:
    return list((await db.execute(
        select(JhscAttendance).where(JhscAttendance.meeting_id == meeting_id)
        .order_by(JhscAttendance.user_name))).scalars())


async def set_attendance(
    db: AsyncSession, meeting: JhscMeeting, marks: dict[uuid.UUID, str]
) -> list[JhscAttendance]:
    rows = {a.id: a for a in await attendance_for(db, meeting.id)}
    for attendance_id, value in marks.items():
        row = rows.get(attendance_id)
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Someone marked is not on this meeting's sheet")
        row.attendance = value
    await db.flush()
    return list(rows.values())


async def publish_meeting(
    db: AsyncSession, meeting: JhscMeeting, *, user_id: uuid.UUID,
) -> JhscMeeting:
    """Minutes out to management, to the members, and onto the noticeboard.

    Refused without minutes: publishing an empty record would satisfy the
    dashboard and nothing else.
    """
    # ★ Minutes are written either in the box or on the configured form, and
    # this only ever looked at the box. Where HSE has chosen a minutes form the
    # box is not even shown, so Publish was permanently refused — reported as
    # "publish the minutes button can not be pressed by the creator of the
    # meeting", which read like a permissions fault and was not one. Adding the
    # form path without moving the test that guards publishing is what caused it.
    on_a_form = False
    if meeting.minutes_submission_id is not None:
        submission = await db.get(FormSubmission, meeting.minutes_submission_id)
        on_a_form = submission is not None and submission.status == "submitted"

    if not (meeting.minutes or "").strip() and not on_a_form:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Write the minutes before publishing — either in the box or on the "
            "minutes form, which has to be submitted. The published record is "
            "what goes on the noticeboard and to the Ministry if asked.")
    meeting.status = "published"
    meeting.published_at = datetime.now(timezone.utc)
    meeting.published_by = user_id
    await db.flush()
    # HSE: minutes go to management and to every member, and onto the board.
    await notify.jhsc_minutes_published(
        db, meeting, [m.user_id for m in await list_members(db) if m.user_id])
    return meeting


async def attendance_by_month(db: AsyncSession, *, year: int) -> list[dict]:
    """What the management dashboard plots: present, against committee size."""
    rows = (await db.execute(
        select(JhscMeeting.id, JhscMeeting.held_on).where(
            JhscMeeting.held_on >= date(year, 1, 1),
            JhscMeeting.held_on < date(year + 1, 1, 1),
        ).order_by(JhscMeeting.held_on)
    )).all()

    out: list[dict] = []
    for meeting_id, held_on in rows:
        counts = dict((await db.execute(
            select(JhscAttendance.attendance, func.count())
            .where(JhscAttendance.meeting_id == meeting_id)
            .group_by(JhscAttendance.attendance)
        )).all())
        invited = sum(counts.values())
        out.append({
            # The meeting's own id. The month is a label, and a committee that
            # met twice in September produces two rows carrying "09" — which the
            # dashboard was using to tell them apart, so React folded them into
            # one and the second meeting's attendance was not drawn.
            "id": str(meeting_id),
            "month": f"{held_on.month:02d}",
            "held_on": held_on.isoformat(),
            "present": counts.get("present", 0),
            "invited": invited,
        })
    return out


# ── Recommendations and the 21-day reply ─────────────────────────────────────

async def list_recommendations(
    db: AsyncSession, *, meeting_id: uuid.UUID | None = None, outstanding_only: bool = False,
) -> list[JhscRecommendation]:
    stmt = select(JhscRecommendation)
    if meeting_id:
        stmt = stmt.where(JhscRecommendation.meeting_id == meeting_id)
    if outstanding_only:
        stmt = stmt.where(JhscRecommendation.responded_at.is_(None))
    return list((await db.execute(
        stmt.order_by(JhscRecommendation.created_at, JhscRecommendation.seq))).scalars())


async def get_recommendation(db: AsyncSession, rec_id: uuid.UUID, *,
                             for_update: bool = False) -> JhscRecommendation:
    # for_update: held until the request commits, so two people answering at
    # once are taken one after the other and the second sees the first.
    rec = await db.get(JhscRecommendation, rec_id, with_for_update=for_update,
                       populate_existing=for_update)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recommendation not found")
    return rec


async def next_recommendation_seq(db: AsyncSession, meeting_id: uuid.UUID) -> int:
    """What the next recommendation at this meeting will be numbered.

    Used by the preview so the draft message carries the number the sent one
    will carry. It is a read; two people previewing at once would be shown the
    same number and whichever records first gets it, which is the same race the
    numbering has always had.
    """
    highest = (await db.execute(
        select(func.max(JhscRecommendation.seq))
        .where(JhscRecommendation.meeting_id == meeting_id))).scalar() or 0
    return highest + 1


async def location_path_of(db: AsyncSession, location_id: uuid.UUID | None) -> str | None:
    """The area's path as it reads today, kept on the recommendation."""
    if location_id is None:
        return None
    path = (await db.execute(
        select(Location.path).where(Location.id == location_id))).scalar()
    if path is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No such plant area")
    return path


STATUS_VOCABULARY = "jhsc_rec_status"


async def first_status(db: AsyncSession):
    """New request, wherever HSE has moved it. Sort order alone is not enough:
    an entry added to the list took sort 0, and every new recommendation then
    opened as "Waiting on Parts"."""
    from app.models.vocabulary import VocabularyItem
    return (await db.execute(select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == STATUS_VOCABULARY,
        VocabularyItem.is_active.is_(True)).order_by(
            (VocabularyItem.code != "new_request"), VocabularyItem.sort_order)
    )).scalars().first()


# ★ Round 12: HSE's JHSC workflow, which replaces round 11's rule.
#   1-2 recommended and assigned            → Being reviewed   (new_request)
#   3-4 the person it was sent to opens it  → Under Review     (maintenance_review)
#   5-6 "what will be done" is submitted    → Work in progress (in_progress)
#   7-10 its corrective action is done and verified by the supervisor
#                                           → Awaiting HSE verification (hse_verification)
#   11  HSE verifies it in the JHSC register → Completed        (completed)
# Each step moves it only from the states before it, so a state somebody set
# by hand further on is not pulled back. A reply that declines or defers
# leaves it where it is.
BEING_REVIEWED, UNDER_REVIEW, IN_PROGRESS = "new_request", "maintenance_review", "in_progress"
HSE_VERIFICATION, COMPLETED = "hse_verification", "completed"


async def _move(db: AsyncSession, rec: JhscRecommendation, code: str, *,
                only_from: tuple[str, ...]) -> bool:
    from app.models.vocabulary import VocabularyItem
    if rec.status_item_id is not None:
        current = await db.get(VocabularyItem, rec.status_item_id)
        if current is not None and current.code not in only_from:
            return False
    item = (await db.execute(select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == STATUS_VOCABULARY, VocabularyItem.code == code,
        VocabularyItem.is_active.is_(True)))).scalars().first()
    if item is None:
        return False
    rec.status_item_id, rec.status_label = item.id, item.label
    return True


async def opened(db: AsyncSession, rec: JhscRecommendation, *, user_id: uuid.UUID) -> None:
    """Step 4: the first time somebody it was sent to opens it."""
    if rec.reviewed_at is not None:
        return
    if not any(str(p.get("id")) == str(user_id) for p in (rec.sent_to or [])):
        return
    rec.reviewed_at = datetime.now(timezone.utc)
    rec.reviewed_by_name = await _name(db, user_id)
    await _move(db, rec, UNDER_REVIEW, only_from=(BEING_REVIEWED,))
    await db.flush()


async def follow_reply(db: AsyncSession, rec: JhscRecommendation) -> None:
    """Step 6: the reply, with what will be done about it."""
    if rec.outcome in ("declined", "deferred"):
        return
    await _move(db, rec, IN_PROGRESS, only_from=(BEING_REVIEWED, UNDER_REVIEW))


async def action_closed(db: AsyncSession, action_id: uuid.UUID) -> None:
    """Step 10: its corrective action has been verified — HSE's turn."""
    rec = (await db.execute(select(JhscRecommendation).where(
        JhscRecommendation.action_id == action_id))).scalars().first()
    if rec is not None:
        await _move(db, rec, HSE_VERIFICATION,
                    only_from=(BEING_REVIEWED, UNDER_REVIEW, IN_PROGRESS))


async def is_completed(db: AsyncSession, rec: JhscRecommendation) -> bool:  # noqa: ARG001
    """Verified by HSE, or already Completed when step 11 arrived (0097).

    ★ Not the state (re-review N1): the people it was sent to may set the
    state, and Completed picked by hand would have skipped HSE's verification
    and left its review task open for good."""
    return rec.hse_verified_at is not None or bool(rec.completed_before_verification)


async def hse_verify(db: AsyncSession, rec: JhscRecommendation, *, user_id: uuid.UUID):
    """Step 11: "HSE role verifies the recommendations using the JHSC registry"."""
    from app.models.action import Action
    from app.services import capa_review
    if await is_completed(db, rec):
        # ★ Review L4: a second call overwrote who verified it and when; and a
        # recommendation completed before step 11 was offered it again.
        raise HTTPException(status.HTTP_409_CONFLICT, "It is already completed")
    if rec.responded_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "It has not been answered yet; there is nothing to verify")
    capa = await db.get(Action, rec.action_id) if rec.action_id else None
    if capa is not None and capa.status not in ("closed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Its corrective action {capa.action_no} is still {capa.status}")
    rec.hse_verified_at = datetime.now(timezone.utc)
    rec.hse_verified_by_name = await _name(db, user_id)
    from app.models.vocabulary import VocabularyItem
    done = (await db.execute(select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == STATUS_VOCABULARY, VocabularyItem.code == COMPLETED,
        VocabularyItem.is_active.is_(True)))).scalars().first()
    if done is not None:
        rec.status_item_id, rec.status_label = done.id, done.label
    await capa_review.close_for(db, task_service.DOC_JHSC, rec.id, user_id)
    await db.flush()
    return rec


async def set_status(db: AsyncSession, rec: JhscRecommendation, item_id: uuid.UUID, *,
                     user_id: uuid.UUID | None = None, may_verify: bool = False):
    """Round 10, TC-11: the recommendation's current state, from HSE's list.

    ★ Re-review N1: Completed is HSE's verification (step 11). Only someone
    who may verify picks it, and picking it is verifying it — stamped and its
    review task closed — so the state and the verification cannot disagree."""
    from app.models.vocabulary import VocabularyItem
    item = await db.get(VocabularyItem, item_id)
    if item is None or item.vocabulary_code != STATUS_VOCABULARY or not item.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "That is not a state on the JHSC recommendation status list")
    if item.code == COMPLETED and not await is_completed(db, rec):
        if not may_verify:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Completed is HSE's verification; HSE sets it")
        # ★ Re-review N7: the same rules as the Verify button — answered, its
        # corrective action closed — not a way round them.
        return await hse_verify(db, rec, user_id=user_id)
    rec.status_item_id, rec.status_label = item.id, item.label
    await db.flush()
    return rec


async def add_recommendation(
    db: AsyncSession, meeting: JhscMeeting, *, text: str, raised_by_name: str | None,
    location_id: uuid.UUID | None = None,
) -> JhscRecommendation:
    """Record a recommendation and start the twenty-one days running."""
    seq = await next_recommendation_seq(db, meeting.id)

    rec = JhscRecommendation(
        id=uuid.uuid4(), meeting_id=meeting.id, seq=seq,
        recommendation=text, raised_by_name=raised_by_name,
        location_id=location_id, location_path=await location_path_of(db, location_id),
    )
    # Round 10: starts at the first state on HSE's list ("New request"), once;
    # whoever works on it moves it on from there.
    first = await first_status(db)
    if first is not None:
        rec.status_item_id, rec.status_label = first.id, first.label
    db.add(rec)
    await db.flush()

    await deadline_service.ensure_jhsc_reply_deadline(
        db, rec, meeting_ref=meeting.meeting_no)
    # ★ The task belongs to the recommendation, not to the meeting.
    #
    # It was `doc_id=meeting.id`, which is one document for every recommendation
    # made at that meeting — so closing "the reply task" could only ever close
    # all of them. Sending a second recommendation closed the first one's task
    # while its twenty-one days were still running and nobody had answered it.
    # Found in review by uniops-9a.
    #
    # `deadlines.py` already uses the recommendation as the document for the
    # statutory task, so both kinds now hang off the same id and are told apart
    # by `task_type` — which every close in this module passes.
    await task_service.open_task(
        db,
        task_type=task_service.TASK_JHSC_REPLY,
        doc_type=DOC_JHSC,
        doc_id=rec.id,
        doc_number=meeting.meeting_no,
        title=f"Reply to JHSC recommendation #{rec.seq}",
        assigned_role="ehs_manager",
        description=("OHSA s.9(20) requires a written response within 21 days. "
                     f"{text[:200]}"),
        priority="normal",
    )
    await notify.jhsc_recommendation(db, meeting, rec)
    return rec


async def respond(
    db: AsyncSession, rec: JhscRecommendation, *, response: str, outcome: str,
    user_id: uuid.UUID, action_id: uuid.UUID | None = None,
) -> JhscRecommendation:
    """The written reply. Stops the statutory clock."""
    if rec.responded_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This recommendation has already been answered; the reply is part of "
            "the record and is not overwritten")

    now = datetime.now(timezone.utc)
    rec.response = response
    rec.outcome = outcome
    rec.responded_at = now
    rec.responded_by = user_id
    rec.responded_by_name = await _name(db, user_id)
    rec.action_id = action_id
    await follow_reply(db, rec)
    await db.flush()

    deadline = await deadline_service.ensure_jhsc_reply_deadline(
        db, rec, meeting_ref=(await get_meeting(db, rec.meeting_id)).meeting_no)
    await deadline_service.satisfy(
        db, deadline, user_id=user_id, user_name=rec.responded_by_name,
        evidence_note=response[:500], when=now)

    # ★ This recommendation's task, and only its own. It used to wait until
    # nothing on the whole meeting was outstanding — a detour that existed
    # solely because every reply task shared the meeting as its document, so
    # closing one closed them all. Now that the task hangs off the
    # recommendation, answering one answers one.
    await task_service.close_tasks_for(
        db, doc_type=DOC_JHSC, doc_id=rec.id,
        task_type=task_service.TASK_JHSC_REPLY, completed_by=user_id)
    # ★ Flushed here, as every other write in this module is. `close_tasks_for`
    # sets attributes and leaves the flush to its caller, and the session this
    # service runs under has autoflush off — so without this a read taken
    # immediately afterwards sees the task still open. In a request that ends in
    # a commit it makes no difference; anywhere else it is the difference between
    # a closed task and one that looks closed only to the object that closed it.
    await db.flush()
    return rec


async def send_recommendation(
    db: AsyncSession, rec: JhscRecommendation, *,
    user_ids: list[uuid.UUID], sender_id: uuid.UUID, message: str | None = None,
) -> JhscRecommendation:
    """Record who the recommendation was put in front of, and tell them.

    Names are stored alongside the ids so the record still reads after
    somebody leaves and the user row is gone.

    Sending again replaces the list rather than appending: a recommendation
    redirected to the right manager should read as having gone to them, not as
    having gone to both.
    """
    people = list((await db.execute(
        select(User).where(User.id.in_(user_ids)))).scalars().all())
    if not people:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "None of those people could be found")

    meeting = await get_meeting(db, rec.meeting_id)
    rec.sent_to = [{"id": str(p.id), "name": p.full_name} for p in people]
    rec.sent_at = datetime.now(timezone.utc)
    rec.sent_by = sender_id
    rec.sent_by_name = await _name(db, sender_id)
    await db.flush()

    # ★ Reported as "the recommendations assigned to a supervisor do not show up
    # in their my actions register". Sending only emailed them and recorded that
    # it had. The reply is a statutory obligation with twenty-one days on it, and
    # this module's rule is that whoever must *do* something holds a task —
    # email is the notice, not the assignment. Raised against each recipient,
    # because each of them was asked.
    #
    # Re-sending replaces the recipients, so their tasks are replaced too: a
    # recommendation redirected to the right manager must stop being the wrong
    # manager's to answer.
    await task_service.close_tasks_for(
        db, doc_type=DOC_JHSC, doc_id=rec.id,
        task_type=task_service.TASK_JHSC_REPLY)
    # ★ UAT 13: a recommendation already answered is sent as a notice only.
    # It used to open a reply task for each recipient regardless — Ken was sent
    # JHSC-2026-0001 #2 on 26 October, six weeks after it was answered, and the
    # page offers no reply on an answered recommendation, so the task could
    # never be completed.
    for person in (people if rec.responded_at is None else []):
        await task_service.open_task(
            db,
            task_type=task_service.TASK_JHSC_REPLY,
            doc_type=DOC_JHSC,
            doc_id=rec.id,
            doc_number=meeting.meeting_no,
            title=f"Reply to JHSC recommendation #{rec.seq}",
            assigned_role="ehs_manager",
            assigned_user_id=person.id,
            description=("OHSA s.9(20) requires a written response within 21 days "
                         f"of the recommendation being made. {rec.recommendation[:300]}"),
        )
    await db.flush()

    await notify.jhsc_recommendation_sent(db, meeting, rec, people, body=message)
    return rec
