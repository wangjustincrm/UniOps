"""Training records, certifications, and the gap report.

The gap report is the one an inspector asks for first, so it is computed from
the same two sources the requirement actually has — courses that apply to
everyone, and courses a person's position requires — rather than from a list
someone maintains by hand and forgets.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import Department, User
from app.models.training import Course, TrainingRecord, WorkerCertification
from app.crud import worker as worker_crud
from app.models.worker import PositionRequirement, WorkerPosition
from app.schemas.training import (
    CertificationIn, CourseGapSummary, GapReport, GapRow, TrainingRecordIn,
)
from app.services import deadlines as deadline_service


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def list_courses(db: AsyncSession, *, statutory_only: bool = False) -> list[Course]:
    stmt = select(Course).where(Course.is_active.is_(True))
    if statutory_only:
        stmt = stmt.where(Course.is_statutory.is_(True))
    return list((await db.execute(stmt.order_by(Course.name))).scalars())


async def get_course(db: AsyncSession, course_id: uuid.UUID) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")
    return course


async def create_course(db: AsyncSession, payload) -> Course:
    """A course of the plant's own. The eight statutory ones are seeded."""
    clash = (await db.execute(
        select(Course).where(Course.code == payload.code))).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A course with the code {payload.code} already exists ({clash.name})")
    course = Course(
        id=uuid.uuid4(), code=payload.code, name=payload.name,
        # Statutory is not something a course can be given here: it follows
        # from the regulation that requires it, and the eight that are come
        # seeded with their clause behind them.
        is_statutory=False,
        applies_to_all=payload.applies_to_all,
        validity_months=payload.validity_months,
    )
    db.add(course)
    await db.flush()
    return course


async def update_course(db: AsyncSession, course: Course, fields: dict) -> Course:
    for key, value in fields.items():
        setattr(course, key, value)
    await db.flush()
    return course


async def record_training(
    db: AsyncSession, payload: TrainingRecordIn, *, recorded_by: uuid.UUID
) -> TrainingRecord:
    course = (await db.execute(
        select(Course).where(Course.id == payload.course_id)
    )).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found")

    user_name = await _name(db, payload.user_id)
    if user_name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")

    expires_on = payload.expires_on
    if expires_on is None and course.validity_months:
        # Approximate a month as 30 days rather than pulling in a date library
        # for a value that is edited by hand whenever the certificate says
        # otherwise.
        expires_on = payload.completed_on + timedelta(days=30 * course.validity_months)

    record = TrainingRecord(
        id=uuid.uuid4(),
        user_id=payload.user_id,
        user_name=user_name,
        course_id=course.id,
        course_label=course.name,
        completed_on=payload.completed_on,
        expires_on=expires_on,
        delivery=payload.delivery,
        certificate_file_id=payload.certificate_file_id,
        recorded_by=recorded_by,
        recorded_by_name=await _name(db, recorded_by),
    )
    db.add(record)
    await db.flush()
    return record


async def list_training(
    db: AsyncSession, *, user_id: uuid.UUID | None = None, course_id: uuid.UUID | None = None
) -> list[TrainingRecord]:
    stmt = select(TrainingRecord)
    if user_id:
        stmt = stmt.where(TrainingRecord.user_id == user_id)
    if course_id:
        stmt = stmt.where(TrainingRecord.course_id == course_id)
    return list((await db.execute(stmt.order_by(TrainingRecord.completed_on.desc()))).scalars())


async def add_certification(
    db: AsyncSession, payload: CertificationIn
) -> WorkerCertification:
    user_name = await _name(db, payload.user_id)
    if user_name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")

    cert = WorkerCertification(
        id=uuid.uuid4(),
        user_id=payload.user_id,
        user_name=user_name,
        cert_type_id=payload.cert_type_id,
        cert_type_label=payload.cert_type_label,
        cert_no=payload.cert_no,
        issuer=payload.issuer,
        issued_on=payload.issued_on,
        expires_on=payload.expires_on,
        file_id=payload.file_id,
        is_blocking=payload.is_blocking,
    )
    db.add(cert)
    await db.flush()
    # Its expiry joins the same clock table as the statutory deadlines.
    await deadline_service.ensure_certification_deadline(db, cert)
    return cert


async def get_certification(db: AsyncSession, cert_id: uuid.UUID) -> WorkerCertification:
    cert = (await db.execute(
        select(WorkerCertification).where(WorkerCertification.id == cert_id)
    )).scalar_one_or_none()
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certification not found")
    return cert


async def renew_certification(
    db: AsyncSession, cert: WorkerCertification, *, issued_on: date | None, expires_on: date,
) -> WorkerCertification:
    """Push the expiry out and reset the clock that was chasing it."""
    if issued_on:
        cert.issued_on = issued_on
    cert.expires_on = expires_on
    await db.flush()
    await deadline_service.ensure_certification_deadline(db, cert)
    return cert


async def list_certifications(
    db: AsyncSession, *, user_id: uuid.UUID | None = None, expiring_within_days: int | None = None,
    today: date | None = None,
) -> list[WorkerCertification]:
    stmt = select(WorkerCertification)
    if user_id:
        stmt = stmt.where(WorkerCertification.user_id == user_id)
    if expiring_within_days is not None:
        cutoff = (today or datetime.now(timezone.utc).date()) + timedelta(days=expiring_within_days)
        stmt = stmt.where(
            WorkerCertification.expires_on.is_not(None),
            WorkerCertification.expires_on <= cutoff,
        )
    return list((await db.execute(stmt.order_by(WorkerCertification.expires_on))).scalars())


async def training_gaps(db: AsyncSession, *, today: date | None = None) -> GapReport:
    """Who is missing which required training.

    Required means either the course applies to everyone, or the person holds a
    position that requires it. A record counts only while it is still valid —
    an expired certificate is a gap, and is reported as a different reason from
    never having taken the course, because the two need different action.
    """
    today = today or datetime.now(timezone.utc).date()

    workers = list((await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.full_name)
    )).scalars())
    departments = {
        d.id: d.name for d in (await db.execute(select(Department))).scalars()
    }
    courses = {c.id: c for c in await list_courses(db)}
    universal = [c for c in courses.values() if c.applies_to_all]

    # position -> required course ids
    required_by_position: dict[uuid.UUID, set[uuid.UUID]] = {}
    for req in (await db.execute(
        select(PositionRequirement).where(
            PositionRequirement.requirement_type == "training",
            PositionRequirement.course_id.is_not(None),
        )
    )).scalars():
        required_by_position.setdefault(req.position_id, set()).add(req.course_id)

    # user -> positions currently held
    positions_by_user: dict[uuid.UUID, set[uuid.UUID]] = {}
    for held in (await db.execute(select(WorkerPosition))).scalars():
        # One definition of "holds this position", shared with the roster
        # screens — an assignment ended today is no longer in force today, so
        # somebody taken off lift trucks this morning stops needing the licence
        # now rather than at midnight.
        if not worker_crud.holds_on(held, today):
            continue
        positions_by_user.setdefault(held.user_id, set()).add(held.position_id)

    # user -> {course_id: latest expiry (None = never expires)}
    held_by_user: dict[uuid.UUID, dict[uuid.UUID, date | None]] = {}
    for rec in (await db.execute(
        select(TrainingRecord).order_by(TrainingRecord.completed_on)
    )).scalars():
        if rec.user_id is None or rec.course_id is None:
            continue
        held_by_user.setdefault(rec.user_id, {})[rec.course_id] = rec.expires_on

    gaps: list[GapRow] = []
    compliant = 0
    # course -> [required_of, missing, expired]. Counted alongside the rows
    # rather than from them, so a course everybody is up to date on still
    # appears — at nought outstanding, which is the answer.
    tally: dict[uuid.UUID, list[int]] = {}
    for worker in workers:
        required = {c.id for c in universal}
        for position_id in positions_by_user.get(worker.id, set()):
            required |= required_by_position.get(position_id, set())

        department = departments.get(worker.department_id) if worker.department_id else None
        held = held_by_user.get(worker.id, {})
        worker_gaps: list[GapRow] = []
        for course_id in sorted(required, key=lambda c: courses[c].name if c in courses else ""):
            course = courses.get(course_id)
            if course is None:
                continue  # a retired course is no longer required of anyone
            counts = tally.setdefault(course_id, [0, 0, 0])
            counts[0] += 1
            if course_id not in held:
                counts[1] += 1
                worker_gaps.append(GapRow(
                    user_id=worker.id, user_name=worker.full_name, course_id=course_id,
                    course_code=course.code, course_name=course.name, reason="missing",
                    department_name=department))
                continue
            expiry = held[course_id]
            if expiry is not None and expiry < today:
                counts[2] += 1
                worker_gaps.append(GapRow(
                    user_id=worker.id, user_name=worker.full_name, course_id=course_id,
                    course_code=course.code, course_name=course.name,
                    reason="expired", expired_on=expiry, department_name=department))
        if worker_gaps:
            gaps.extend(worker_gaps)
        else:
            compliant += 1

    considered = len(workers)
    return GapReport(
        as_of=today,
        workers_considered=considered,
        workers_compliant=compliant,
        compliance_rate=round(compliant / considered, 4) if considered else 1.0,
        gaps=gaps,
        # Worst first: the course the plant is furthest behind on is the one to
        # book a room for.
        courses=sorted(
            (
                CourseGapSummary(
                    course_id=cid, course_code=courses[cid].code,
                    course_name=courses[cid].name,
                    required_of=n[0], missing=n[1], expired=n[2],
                )
                for cid, n in tally.items() if cid in courses
            ),
            key=lambda c: (-(c.missing + c.expired), c.course_name),
        ),
    )
