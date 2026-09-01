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
from app.services import tasks as task_service
from app.services.numbering import next_number

DOC_COURSE = "ehs_crs"


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


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
    db: AsyncSession, delivery: CourseDelivery, user_ids: list[uuid.UUID]
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
            user_name=user.full_name, department_name=dept,
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


async def mark_results(
    db: AsyncSession, delivery: CourseDelivery, results: dict[uuid.UUID, str],
    *, marker_id: uuid.UUID,
) -> list[CourseEnrolment]:
    """Record pass / fail / absent, and issue a training record for each pass.

    HSE marks this by hand — there is no online test — so the only rule the
    system enforces is that a pass produces exactly one register entry.
    """
    marker_name = await _name(db, marker_id)
    now = datetime.now(timezone.utc)
    rows = await roster(db, delivery.id)
    by_id = {e.id: e for e in rows}

    changed: list[CourseEnrolment] = []
    for enrolment_id, result in results.items():
        enrolment = by_id.get(enrolment_id)
        if enrolment is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Someone marked is not on this roster")
        enrolment.result = result
        enrolment.marked_by = marker_id
        enrolment.marked_by_name = marker_name
        enrolment.marked_at = now
        changed.append(enrolment)

        if result == "passed" and enrolment.training_record_id is None:
            record = await _issue_record(db, delivery, enrolment, recorded_by=marker_id,
                                         recorded_by_name=marker_name)
            enrolment.training_record_id = record.id

    await db.flush()
    return changed


async def _issue_record(
    db: AsyncSession, delivery: CourseDelivery, enrolment: CourseEnrolment,
    *, recorded_by: uuid.UUID, recorded_by_name: str | None,
) -> TrainingRecord:
    """The row that appears in this person's file and in the gap report."""
    course = await db.get(Course, delivery.course_id) if delivery.course_id else None
    expires_on = None
    if course and course.validity_months:
        # A month as thirty days, matching how records entered by hand are
        # dated. The date is editable afterwards when the certificate differs.
        expires_on = delivery.session_date + timedelta(days=30 * course.validity_months)

    record = TrainingRecord(
        id=uuid.uuid4(),
        user_id=enrolment.user_id,
        user_name=enrolment.user_name,
        course_id=delivery.course_id,
        course_label=delivery.course_label,
        completed_on=delivery.session_date,
        expires_on=expires_on,
        delivery="internal",
        recorded_by=recorded_by,
        recorded_by_name=recorded_by_name,
    )
    db.add(record)
    await db.flush()
    return record


async def complete(db: AsyncSession, delivery: CourseDelivery) -> CourseDelivery:
    """Close the session. Everyone still unmarked is recorded as absent.

    Left as `enrolled`, they would count as neither trained nor untrained and
    the session would sit half-finished forever. Absent is the honest reading
    of "was on the list and nobody said they passed".
    """
    if delivery.status == "completed":
        return delivery
    for enrolment in await roster(db, delivery.id):
        if enrolment.result == "enrolled":
            enrolment.result = "absent"
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
    today = today or datetime.now(timezone.utc).date()
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
        for enrolment in await roster(db, delivery.id):
            if enrolment.user_id is None:
                continue
            await task_service.open_task(
                db,
                task_type=task_service.TASK_COURSE_ATTEND,
                doc_type=DOC_COURSE,
                doc_id=delivery.id,
                doc_number=delivery.delivery_no,
                title=f"Tomorrow: {delivery.title}",
                assigned_role="worker",
                assigned_user_id=enrolment.user_id,
                description=f"{delivery.title} starts at {when}{where}.",
                priority="normal",
            )
        delivery.reminded_on = today

    await db.flush()
    return list(due)
