"""Running a course session, from creating it to issuing the certificates.

HSE's sequence, in order, and where each step lands:

1. create the session and pick the course       `create_delivery`
2. attach the course material                   `PATCH` the delivery
3. set the date, time and place                 `create_delivery` / `PATCH`
4. HSE names a supervisor to administer it      `coordinator_id`
5. that supervisor adds the attendees           `enrol`
6. the attendees are reminded the day before    `remind_upcoming`, on the sweep
7. HSE records who taught it                    `instructor_name`
8. HSE marks each attendee passed or failed     `mark_results`

Step 8 is where a session stops being an event and becomes evidence: passing
writes a row into the training register and stores its id on the enrolment, so
the two can never disagree about whether a certificate was issued. Re-marking
somebody who already passed does not issue a second one.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import CourseDelivery, CourseEnrolment
from app.models.mirrors import Department, User
from app.models.training import Course, TrainingRecord
from app.services import notify
from app.services import tasks as task_service
from app.services.numbering import next_number
from app.services.day_range import plant_today

DOC_COURSE = "ehs_crs"


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


def length_minutes(delivery: CourseDelivery) -> int | None:
    """How long the session ran, from its start and end times.

    Round 6: "we need to track the length of time the training is ... this
    course length also needs to be on any reports." None until both are known;
    the end is filled in by the first person to finish the quiz when nobody
    typed one (app.crud.quiz).
    """
    if delivery.start_time is None or delivery.end_time is None:
        return None
    start = delivery.start_time.hour * 60 + delivery.start_time.minute
    end = delivery.end_time.hour * 60 + delivery.end_time.minute
    return end - start if end > start else None


async def get_delivery(db: AsyncSession, delivery_id: uuid.UUID) -> CourseDelivery:
    delivery = await db.get(CourseDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course session not found")
    return delivery


async def list_deliveries(
    db: AsyncSession, *,
    course_id: uuid.UUID | None = None,
    coordinator_id: uuid.UUID | None = None,
    attendee_id: uuid.UUID | None = None,
    status_in: list[str] | None = None,
    limit: int = 100,
) -> list[CourseDelivery]:
    stmt = select(CourseDelivery)
    if course_id:
        stmt = stmt.where(CourseDelivery.course_id == course_id)
    if coordinator_id:
        stmt = stmt.where(CourseDelivery.coordinator_id == coordinator_id)
    if attendee_id:
        # "Courses I am booked on" — a scalar subquery rather than a join, so
        # the delivery rows come back once each regardless of roster size.
        stmt = stmt.where(CourseDelivery.id.in_(
            select(CourseEnrolment.delivery_id).where(CourseEnrolment.user_id == attendee_id)
        ))
    if status_in:
        stmt = stmt.where(CourseDelivery.status.in_(status_in))
    return list((await db.execute(
        stmt.order_by(CourseDelivery.session_date.desc()).limit(limit))).scalars())


async def create_delivery(
    db: AsyncSession, *, course_id: uuid.UUID, title: str | None, session_date: date,
    start_time=None, end_time=None, location_text: str | None = None,
    location_id: uuid.UUID | None = None, coordinator_id: uuid.UUID | None = None,
    notes: str | None = None, creator_id: uuid.UUID,
) -> CourseDelivery:
    course = await db.get(Course, course_id)
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")

    delivery = CourseDelivery(
        id=uuid.uuid4(),
        delivery_no=await next_number(db, "course_delivery"),
        course_id=course.id,
        course_label=course.name,
        title=title or course.name,
        session_date=session_date,
        start_time=start_time,
        end_time=end_time,
        location_text=location_text,
        location_id=location_id,
        coordinator_id=coordinator_id,
        coordinator_name=await _name(db, coordinator_id),
        # The course's standing handout, unless this sitting attaches its own.
        material_file_id=course.material_file_id,
        material_filename=course.material_filename,
        notes=notes,
        created_by=creator_id,
        created_by_name=await _name(db, creator_id),
    )
    db.add(delivery)
    await db.flush()

    if coordinator_id:
        await _open_coordinator_task(db, delivery)
    return delivery


async def _open_coordinator_task(db: AsyncSession, delivery: CourseDelivery) -> None:
    """Tell the nominated supervisor they own this session's roster.

    Without this the nomination is a name in a field: HSE picks somebody, and
    that person has no way of finding out until the day.
    """
    await task_service.open_task(
        db,
        task_type=task_service.TASK_COURSE_ADMIN,
        doc_type=DOC_COURSE,
        doc_id=delivery.id,
        doc_number=delivery.delivery_no,
        title=f"Add attendees: {delivery.title}",
        assigned_role="area_supervisor",
        assigned_user_id=delivery.coordinator_id,
        description=(f"{delivery.title} runs on {delivery.session_date.isoformat()}"
                     f"{' at ' + delivery.location_text if delivery.location_text else ''}. "
                     "Add the people from your area who are attending."),
        priority="normal",
    )


async def set_coordinator(
    db: AsyncSession, delivery: CourseDelivery, coordinator_id: uuid.UUID | None
) -> CourseDelivery:
    """Hand the session to a supervisor, or to a different one.

    Reassigning closes the previous holder's task before opening the new one —
    otherwise the person who was replaced keeps a to-do for work that is no
    longer theirs, which is how a task inbox stops being believed.
    """
    if delivery.coordinator_id == coordinator_id:
        return delivery
    await task_service.close_tasks_for(
        db, doc_type=DOC_COURSE, doc_id=delivery.id,
        task_type=task_service.TASK_COURSE_ADMIN)
    delivery.coordinator_id = coordinator_id
    delivery.coordinator_name = await _name(db, coordinator_id)
    await db.flush()
    if coordinator_id:
        await _open_coordinator_task(db, delivery)
    return delivery


async def roster(db: AsyncSession, delivery_id: uuid.UUID) -> list[CourseEnrolment]:
    return list((await db.execute(
        select(CourseEnrolment).where(CourseEnrolment.delivery_id == delivery_id)
        .order_by(CourseEnrolment.user_name)
    )).scalars())


async def enrol(
    db: AsyncSession, delivery: CourseDelivery, user_ids: list[uuid.UUID],
    *, via: str = "roster",
) -> list[CourseEnrolment]:
    """Add people to the roster. Adding somebody twice is a no-op, not an error.

    The supervisor building this list works from memory and adds a few at a
    time; refusing the whole request because one name is already on it would
    make that unusable.
    """
    if delivery.status != "scheduled":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{delivery.delivery_no} is {delivery.status} — its roster is closed")

    already = {e.user_id for e in await roster(db, delivery.id) if e.user_id}
    added: list[CourseEnrolment] = []
    for user_id in user_ids:
        if user_id in already:
            continue
        user = await db.get(User, user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such person: {user_id}")
        dept = (await db.execute(
            select(Department.name).where(Department.id == user.department_id))).scalar()
        enrolment = CourseEnrolment(
            id=uuid.uuid4(), delivery_id=delivery.id, user_id=user.id,
            user_name=user.full_name, department_name=dept, joined_via=via,
        )
        db.add(enrolment)
        added.append(enrolment)
        already.add(user_id)

    await db.flush()
    return added


async def withdraw(db: AsyncSession, delivery: CourseDelivery, enrolment_id: uuid.UUID) -> None:
    enrolment = await db.get(CourseEnrolment, enrolment_id)
    if enrolment is None or enrolment.delivery_id != delivery.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on this session's roster")
    if enrolment.training_record_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This person has already been certified from this session; "
            "removing them would leave the certificate with nothing behind it")
    await db.delete(enrolment)
    await db.flush()


async def quiz_for_course(db: AsyncSession, course_id: uuid.UUID | None):
    """The quiz a session of this course is tested on, if it has one.

    The most recently written active one: HSE replaces a quiz by writing a new
    one, and the old stays in the list until it is retired.
    """
    from app.crud.quiz import has_questions  # quiz imports this module
    from app.models.quiz import Quiz
    if course_id is None:
        return None
    # One with no questions yet cannot be passed, so it holds nothing.
    return (await db.execute(
        select(Quiz).where(Quiz.course_id == course_id, Quiz.is_active.is_(True),
                           has_questions())
        .order_by(Quiz.created_at.desc()).limit(1))).scalars().first()


async def mark_results(
    db: AsyncSession, delivery: CourseDelivery, results: dict[uuid.UUID, str],
    *, marker_id: uuid.UUID,
) -> list[CourseEnrolment]:
    """Record pass / fail / absent.

    ★ Round 6: "in order for an attendee to pass the training session, they must
    have passed the quiz and the run by person must also mark them as passed
    before saving and closing the session and generating a training
    certificate." So a pass needs the quiz mark where the course has a quiz, and
    the record is issued when the session closes — see complete() — not here.
    A closed session is read only (round 10).
    """
    assert_not_closed(delivery)
    marker_name = await _name(db, marker_id)
    now = datetime.now(timezone.utc)
    rows = await roster(db, delivery.id)
    by_id = {e.id: e for e in rows}
    has_quiz = (await quiz_for_course(db, delivery.course_id)) is not None

    changed: list[CourseEnrolment] = []
    for enrolment_id, result in results.items():
        enrolment = by_id.get(enrolment_id)
        if enrolment is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Someone marked is not on this roster")
        if (result == "passed" and has_quiz and enrolment.quiz_passed_at is None
                and enrolment.result != "passed"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{enrolment.user_name} has not passed the quiz yet. Passing the "
                "session needs the quiz first — online, or marked as passed here "
                "for a quiz done on paper.")
        enrolment.result = result
        enrolment.marked_by = marker_id
        enrolment.marked_by_name = marker_name
        enrolment.marked_at = now
        changed.append(enrolment)

    await db.flush()
    return changed


async def set_quiz_passed(
    db: AsyncSession, delivery: CourseDelivery, enrolment_id: uuid.UUID,
    passed: bool, *, marker_id: uuid.UUID,
) -> CourseEnrolment:
    """The "Quiz passed" mark, set by hand — for a quiz done on paper.

    Taking it away from somebody already marked passed is refused: their pass
    rests on it, and the order is to change the result first.
    """
    enrolment = await db.get(CourseEnrolment, enrolment_id)
    if enrolment is None or enrolment.delivery_id != delivery.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not on this session's roster")
    if passed:
        if enrolment.quiz_passed_at is None:
            enrolment.quiz_passed_at = datetime.now(timezone.utc)
            enrolment.quiz_marked_by_name = await _name(db, marker_id)
    else:
        if enrolment.result == "passed":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{enrolment.user_name} is marked passed, which rests on the quiz. "
                "Change their result first.")
        enrolment.quiz_passed_at = None
        enrolment.quiz_marked_by_name = None
    await db.flush()
    return enrolment


async def _issue_record(
    db: AsyncSession, delivery: CourseDelivery, enrolment: CourseEnrolment,
    *, recorded_by: uuid.UUID, recorded_by_name: str | None,
) -> TrainingRecord:
    """The row that appears in this person's file and in the gap report."""
    course = await db.get(Course, delivery.course_id) if delivery.course_id else None
    # Round 9: the course's own period, less a day, as for a record entered by
    # hand (services/validity.py). Editable afterwards when the certificate differs.
    from app.services import validity
    expires_on = validity.expires_on(delivery.session_date, course.validity_months if course else None)

    record = TrainingRecord(
        id=uuid.uuid4(),
        user_id=enrolment.user_id,
        user_name=enrolment.user_name,
        course_id=delivery.course_id,
        course_label=delivery.course_label,
        completed_on=delivery.session_date,
        expires_on=expires_on,
        delivery="internal",
        # Which session produced it. Round 5, TC-08 wants the report to name who
        # ran the course and who taught it, and both live on the session.
        delivery_id=delivery.id,
        recorded_by=recorded_by,
        recorded_by_name=recorded_by_name,
    )
    db.add(record)
    await db.flush()
    return record


def assert_not_closed(delivery: CourseDelivery) -> None:
    """★ Round 10, TC-05: "once a course has completed and the run by person has
    pressed the save and close the session button, make the course and all
    buttons read only. currently when viewing a held course, the HSE role can
    still click and change the passed, fail, did not attend buttons and save the
    record." A closed session is the record its certificates were issued from;
    a correction is made on the training record itself."""
    if delivery.status == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{delivery.delivery_no} has been closed and its certificates issued, so it "
            "is read only. To correct a result, HSE reopens the session saying why, "
            "and closes it again.")


async def reopen(db: AsyncSession, delivery: CourseDelivery, *, reason: str,
                 user_id: uuid.UUID) -> CourseDelivery:
    """★ Review (uniops-8b): the way to correct a closed session.

    The refusal above used to send HSE to the training record, where nothing
    can be changed. Reopened, the session takes marks again; closing it again
    issues what is now owed and withdraws what is not (see complete())."""
    if delivery.status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{delivery.delivery_no} is not closed")
    delivery.status = "scheduled"
    delivery.completed_at = None
    delivery.reopened_at = datetime.now(timezone.utc)
    delivery.reopened_by_name = await _name(db, user_id)
    delivery.reopen_reason = reason.strip()
    await db.flush()
    return delivery


async def complete(db: AsyncSession, delivery: CourseDelivery, *,
                   absent_ok: bool = False) -> CourseDelivery:
    """Close the session: issue the passes, and record the unmarked as absent.

    Left as `enrolled`, they would count as neither trained nor untrained and
    the session would sit half-finished forever. Absent is the honest reading
    of "was on the list and nobody said they passed".
    """
    if delivery.status == "completed":
        return delivery
    # ★ A cancelled session is not closed into a completed one. Closing now
    # issues certificates, so this would have certified a session that never ran.
    if delivery.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{delivery.delivery_no} was cancelled; it cannot be closed")
    rows = await roster(db, delivery.id)
    # ★ Somebody who passed the quiz and has not been marked would be recorded
    # absent by closing — with a passed attempt at this very session on file.
    # Named, and closed only when the person running it says so.
    waiting = [e.user_name for e in rows if e.result == "enrolled" and e.quiz_passed_at]
    if waiting and not absent_ok:
        # A code beside the words: the page offers "Close anyway" for this
        # refusal and no other (a cancelled session is a 409 too).
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "unmarked_passes", "names": waiting,
            "message": f"{', '.join(waiting)} passed the quiz and "
                       f"{'is' if len(waiting) == 1 else 'are'} not marked yet. "
                       "Mark them, or close anyway to record them as not attended."})
    # ★ Review (uniops-8b): after a reopening, somebody marked passed by
    # mistake — whose record and certificate this session issued — may now be
    # failed or absent. The record is this session's own, so it goes: their
    # certificate is rendered from it, and the expiry figures and notices read
    # it. Its files are evidence, so one that has any is refused rather than
    # taken with it.
    from app.models.course import CourseMaterial
    for enrolment in rows:
        if enrolment.result == "passed" or enrolment.training_record_id is None:
            continue
        record = await db.get(TrainingRecord, enrolment.training_record_id)
        if record is not None and record.delivery_id == delivery.id:
            if (await db.execute(select(CourseMaterial.id).where(
                    CourseMaterial.record_id == record.id).limit(1))).first():
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{enrolment.user_name} is no longer marked passed, and the training "
                    "record this session issued them has files on it. Move or remove "
                    "those files first.")
            await db.delete(record)
        enrolment.training_record_id = None
    for enrolment in rows:
        if enrolment.result == "enrolled":
            enrolment.result = "absent"
        # ★ The certificate is issued here, on "Save and close the session",
        # for everybody marked passed. Round 6: "a person does not pass and get a
        # certificate until the run by person presses the save and close the
        # session button."
        elif enrolment.result == "passed" and enrolment.training_record_id is None:
            record = await _issue_record(
                db, delivery, enrolment, recorded_by=enrolment.marked_by,
                recorded_by_name=enrolment.marked_by_name)
            enrolment.training_record_id = record.id
    delivery.status = "completed"
    delivery.completed_at = datetime.now(timezone.utc)
    await task_service.close_tasks_for(db, doc_type=DOC_COURSE, doc_id=delivery.id)
    await db.flush()
    return delivery


async def cancel(db: AsyncSession, delivery: CourseDelivery) -> CourseDelivery:
    if delivery.status == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session has been marked complete and certificates issued from it")
    delivery.status = "cancelled"
    await task_service.close_tasks_for(db, doc_type=DOC_COURSE, doc_id=delivery.id)
    await db.flush()
    return delivery


async def remind_upcoming(db: AsyncSession, *, today: date | None = None) -> list[CourseDelivery]:
    """The day-before reminder, one task per attendee.

    Driven by `reminded_on` rather than by "is the session tomorrow": the sweep
    runs every few minutes, and a condition alone would raise a task on every
    tick of that day. Acting on the change makes it once.
    """
    today = today or plant_today()
    tomorrow = today + timedelta(days=1)

    due = (await db.execute(
        select(CourseDelivery).where(
            CourseDelivery.status == "scheduled",
            CourseDelivery.session_date == tomorrow,
            CourseDelivery.reminded_on.is_(None),
        )
    )).scalars().all()

    for delivery in due:
        when = delivery.start_time.strftime("%H:%M") if delivery.start_time else "the usual time"
        where = f" at {delivery.location_text}" if delivery.location_text else ""
        attending = await roster(db, delivery.id)
        # HSE's step six. One message to the room, and a task each — the task
        # is what shows up beside their other work on the morning.
        await notify.course_tomorrow(
            db, delivery, [e.user_id for e in attending if e.user_id])
        for enrolment in attending:
            if enrolment.user_id is None:
                continue
            await task_service.open_task(
                db,
                task_type=task_service.TASK_COURSE_ATTEND,
                doc_type=DOC_COURSE,
                doc_id=delivery.id,
                doc_number=delivery.delivery_no,
                title=f"Tomorrow: {delivery.title}",
                assigned_role=task_service.ROLE_ANY_EMPLOYEE,
                assigned_user_id=enrolment.user_id,
                description=f"{delivery.title} starts at {when}{where}.",
                priority="normal",
            )
        delivery.reminded_on = today

    await db.flush()
    return list(due)
