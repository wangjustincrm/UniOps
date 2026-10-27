"""Worker safety profiles, and the position matrix that decides who needs what.

The position half of this exists to make the training gap report tell the
truth. That report combines two sources of "required": courses that apply to
everyone, and courses a person's position requires. HSE named four of each —
WHMIS, Lockout Tagout, Safety Awareness and Working at Heights for everyone;
Forklift, Mobile Equipment Work Platform, Confined Space and First Aid for
particular jobs. Until something could write positions, the second four could
never be required of anybody, so the report an inspector asks for first was
quietly answering half the question.

Profiles are a 1:1 side table on `users` rather than columns on it, and the
medical fields are separated again at the API layer: `medical_notes` is PHIPA
material and is not returned to a caller holding only `ehs.worker.read`.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import User
from app.models.training import Course
from app.models.worker import (
    JobPosition, PositionRequirement, WorkerPosition, WorkerProfile,
)
from app.services.day_range import plant_today


# ── Profiles ─────────────────────────────────────────────────────────────────

async def get_profile(db: AsyncSession, user_id: uuid.UUID) -> WorkerProfile | None:
    return (await db.execute(
        select(WorkerProfile).where(WorkerProfile.user_id == user_id))).scalar_one_or_none()


async def list_profiles(db: AsyncSession) -> list[WorkerProfile]:
    return list((await db.execute(select(WorkerProfile))).scalars())


async def upsert_profile(
    db: AsyncSession, user_id: uuid.UUID, fields: dict
) -> WorkerProfile:
    """Create or update. A profile is optional data about an existing person,
    so there is no separate "create" — the first save makes the row."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")

    profile = await get_profile(db, user_id)
    if profile is None:
        profile = WorkerProfile(user_id=user_id)
        db.add(profile)

    if fields.get("employee_no"):
        clash = (await db.execute(
            select(WorkerProfile).where(
                WorkerProfile.employee_no == fields["employee_no"],
                WorkerProfile.user_id != user_id,
            ))).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Employee number {fields['employee_no']} already belongs to somebody else")

    for key, value in fields.items():
        setattr(profile, key, value)
    await db.flush()
    return profile


# ── Positions ────────────────────────────────────────────────────────────────

async def list_positions(db: AsyncSession, *, active_only: bool = True) -> list[JobPosition]:
    stmt = select(JobPosition)
    if active_only:
        stmt = stmt.where(JobPosition.is_active.is_(True))
    return list((await db.execute(stmt.order_by(JobPosition.name))).scalars())


async def get_position(db: AsyncSession, position_id: uuid.UUID) -> JobPosition:
    position = await db.get(JobPosition, position_id)
    if position is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Position not found")
    return position


async def create_position(db: AsyncSession, *, code: str, name: str) -> JobPosition:
    clash = (await db.execute(
        select(JobPosition).where(JobPosition.code == code))).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"There is already a position with code {code}")
    position = JobPosition(id=uuid.uuid4(), code=code, name=name)
    db.add(position)
    await db.flush()
    return position


async def retire_position(db: AsyncSession, position: JobPosition) -> JobPosition:
    """Deactivate rather than delete.

    People hold it historically and the gap report reads those assignments;
    deleting would take that history with it and silently change what past
    compliance looked like.
    """
    position.is_active = False
    await db.flush()
    return position


async def requirements_for(
    db: AsyncSession, position_id: uuid.UUID
) -> list[PositionRequirement]:
    return list((await db.execute(
        select(PositionRequirement)
        .where(PositionRequirement.position_id == position_id)
        .order_by(PositionRequirement.requirement_type, PositionRequirement.label)
    )).scalars())


async def set_requirements(
    db: AsyncSession, position: JobPosition, requirements: list[dict]
) -> list[PositionRequirement]:
    """Replace what this position requires.

    Wholesale rather than a diff: the editor is a checklist of courses plus a
    short list of PPE, and it is re-submitted whole every time.
    """
    for existing in await requirements_for(db, position.id):
        await db.delete(existing)
    await db.flush()

    rows: list[PositionRequirement] = []
    for req in requirements:
        label = req.get("label")
        if req.get("requirement_type") == "training" and req.get("course_id"):
            course = await db.get(Course, req["course_id"])
            if course is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "A required course no longer exists")
            # Snapshot the name so a renamed course does not rewrite the
            # matrix, per the module-wide rule.
            label = label or course.name
        row = PositionRequirement(
            id=uuid.uuid4(), position_id=position.id,
            requirement_type=req.get("requirement_type", "training"),
            course_id=req.get("course_id"),
            vocabulary_item_id=req.get("vocabulary_item_id"),
            label=label,
            is_mandatory=req.get("is_mandatory", True),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


# ── Who holds which position ─────────────────────────────────────────────────

def holds_on(assignment: WorkerPosition, day: date) -> bool:
    """Whether this assignment is in force on `day`.

    The single definition of "holds this position", used by the holders list,
    by a person's own positions, and by the training gap report — three places
    that must agree, and that disagreed until this was pulled out: an
    assignment ended today still counted as held, so removing somebody left
    them in the compliance report for the rest of the day.
    """
    if assignment.effective_from > day:
        return False
    return assignment.effective_to is None or assignment.effective_to > day

async def holders_of(
    db: AsyncSession, position_id: uuid.UUID, *, today: date | None = None
) -> list[tuple[WorkerPosition, str]]:
    """Current holders, with their names. Past holders are excluded here but
    kept in the table — the gap report needs to know who held what when."""
    today = today or plant_today()
    rows = list((await db.execute(
        select(WorkerPosition).where(WorkerPosition.position_id == position_id))).scalars())
    out: list[tuple[WorkerPosition, str]] = []
    for row in rows:
        if not holds_on(row, today):
            continue
        name = (await db.execute(
            select(User.full_name).where(User.id == row.user_id))).scalar()
        out.append((row, name or "Unknown"))
    out.sort(key=lambda pair: pair[1])
    return out


async def positions_of(
    db: AsyncSession, user_id: uuid.UUID, *, today: date | None = None
) -> list[WorkerPosition]:
    today = today or plant_today()
    rows = list((await db.execute(
        select(WorkerPosition).where(WorkerPosition.user_id == user_id))).scalars())
    return [r for r in rows if holds_on(r, today)]


async def assign(
    db: AsyncSession, *, position_id: uuid.UUID, user_id: uuid.UUID,
    effective_from: date | None = None,
) -> WorkerPosition:
    await get_position(db, position_id)
    if await db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")

    effective_from = effective_from or plant_today()
    for held in await positions_of(db, user_id):
        if held.position_id == position_id:
            return held

    # An assignment that was ended rather than deleted is still on file, and
    # the row is unique on (person, position, start date). Taking somebody off
    # a position and putting them back the same day therefore collided with
    # their own ended row and answered 500 — which is what the second
    # acceptance round found: "removing staff and then adding them back returns
    # server error msg".
    #
    # Revived rather than duplicated, because that is what it means: removed
    # and re-added the same day is a correction, not two spells in the job. A
    # row from an earlier date is left alone — that is a real second spell, and
    # the new one starts today.
    ended = (await db.execute(
        select(WorkerPosition).where(
            WorkerPosition.user_id == user_id,
            WorkerPosition.position_id == position_id,
            WorkerPosition.effective_from == effective_from,
        )
    )).scalar_one_or_none()
    if ended is not None:
        ended.effective_to = None
        await db.flush()
        return ended

    row = WorkerPosition(
        id=uuid.uuid4(), user_id=user_id, position_id=position_id,
        effective_from=effective_from,
    )
    db.add(row)
    await db.flush()
    return row


async def unassign(
    db: AsyncSession, assignment_id: uuid.UUID, *, on: date | None = None
) -> WorkerPosition:
    """End an assignment rather than delete it.

    Somebody who held the forklift position last year genuinely needed the
    licence then; deleting the row would make last year's compliance report
    retroactively read clean.
    """
    row = await db.get(WorkerPosition, assignment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    row.effective_to = on or plant_today()
    await db.flush()
    return row
