"""Emergency drills, and who is qualified to respond.

Two things live here and only one of them is a table.

**Drills** are recorded, and the record is a measurement — the evacuation time
and whether everyone was accounted for. `trend` exists because a single drill
time means nothing: three minutes ten is good or bad only against the last one,
and the question always asked after a drill is "are we getting better".

**The responder roster is computed, not maintained.** Who is a qualified first
aider is already known — it is whoever holds a current First Aid record — and a
second hand-kept list would drift from it within a month and be wrong in the
direction that matters. So `responders()` reads the training register, and
`coverage()` answers the question Reg 1101 actually asks: is every shift
covered, and by whom.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drill import Drill, DrillParticipant
from app.models.mirrors import Location, User
from app.models.training import Course, TrainingRecord
from app.models.worker import WorkerProfile
from app.services.numbering import next_number
from app.services.day_range import plant_today

# The course whose holders are first aiders. Matched on code rather than name
# so renaming the course in Settings does not empty the roster.
FIRST_AID_COURSE_CODE = "FIRSTAID"

DRILL_TYPES = ("fire", "evacuation", "spill", "rescue", "medical", "shelter", "other")


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


# ── Drills ───────────────────────────────────────────────────────────────────

async def list_drills(
    db: AsyncSession, *, status_in: list[str] | None = None,
    drill_type: str | None = None, year: int | None = None, limit: int = 200,
) -> list[Drill]:
    stmt = select(Drill)
    if status_in:
        stmt = stmt.where(Drill.status.in_(status_in))
    if drill_type:
        stmt = stmt.where(Drill.drill_type == drill_type)
    if year:
        stmt = stmt.where(Drill.held_on >= date(year, 1, 1),
                          Drill.held_on < date(year + 1, 1, 1))
    return list((await db.execute(
        stmt.order_by(Drill.held_on.desc().nullslast(),
                      Drill.planned_for.desc().nullslast()).limit(limit))).scalars())


async def get_drill(db: AsyncSession, drill_id: uuid.UUID) -> Drill:
    drill = await db.get(Drill, drill_id)
    if drill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Drill not found")
    return drill


async def create_drill(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> Drill:
    if payload.get("drill_type") not in DRILL_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown drill type. One of: {', '.join(DRILL_TYPES)}")

    areas: list[dict] = []
    for area_id in payload.get("area_ids") or []:
        loc = await db.get(Location, area_id)
        if loc is not None:
            areas.append({"id": str(loc.id), "path": loc.path})

    drill = Drill(
        id=uuid.uuid4(),
        drill_no=await next_number(db, "drill"),
        drill_type=payload["drill_type"],
        scenario=payload["scenario"],
        planned_for=payload.get("planned_for"),
        held_on=payload.get("held_on"),
        started_at=payload.get("started_at"),
        ended_at=payload.get("ended_at"),
        areas=areas,
        is_announced=payload.get("is_announced", True),
        created_by=creator_id,
        created_by_name=await _name(db, creator_id),
    )
    if drill.held_on:
        drill.status = "held"
    db.add(drill)
    await db.flush()
    return drill


def assert_not_evaluated(drill: Drill, what: str = "the record") -> None:
    """An evaluated drill is closed to changes — all of them.

    The rule lived inside `update_drill`, so it covered the drill's own fields
    and nothing else: the photographs hung off a separate table with a separate
    endpoint, and evidence could be deleted from a closed drill by anybody
    holding a token. A record that says "not edited afterwards" while its
    attachments can be removed is making a claim it does not keep.
    """
    if drill.status == "evaluated":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{drill.drill_no} has been evaluated. {what.capitalize()} is not "
            "changed afterwards.")


async def update_drill(db: AsyncSession, drill: Drill, fields: dict) -> Drill:
    assert_not_evaluated(drill, "the record of what happened")

    if "area_ids" in fields:
        areas: list[dict] = []
        for area_id in fields.pop("area_ids") or []:
            loc = await db.get(Location, area_id)
            if loc is not None:
                areas.append({"id": str(loc.id), "path": loc.path})
        drill.areas = areas

    if fields.get("root_cause_id"):
        from app.crud.vocabulary import label_for
        fields["root_cause_label"] = await label_for(db, "root_cause", fields["root_cause_id"])
    for key, value in fields.items():
        setattr(drill, key, value)

    # Derived rather than asked for twice: whoever is entering a drill has the
    # two clock times in front of them, not a stopwatch total.
    if drill.started_at and drill.ended_at and drill.evacuation_seconds is None:
        drill.evacuation_seconds = int((drill.ended_at - drill.started_at).total_seconds())
    if drill.held_on and drill.status == "planned":
        drill.status = "held"

    await db.flush()
    return drill


async def evaluate(
    db: AsyncSession, drill: Drill, *, user_id: uuid.UUID,
) -> Drill:
    """Close the drill off.

    Refused without a muster answer. A drill that does not say whether everyone
    was accounted for has not measured the thing it exists to measure, and
    leaving it null would let it count towards the year's total anyway.
    """
    if drill.held_on is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Record the date it was held before evaluating it.")
    if drill.all_accounted_for is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Say whether everyone was accounted for at the muster point. "
            "That is the drill's result — leaving it blank makes the record "
            "count as a completed drill while answering nothing.")

    drill.status = "evaluated"
    drill.evaluated_at = datetime.now(timezone.utc)
    drill.evaluated_by = user_id
    drill.evaluated_by_name = await _name(db, user_id)
    await db.flush()
    return drill


async def participants_for(db: AsyncSession, drill_id: uuid.UUID) -> list[DrillParticipant]:
    return list((await db.execute(
        select(DrillParticipant).where(DrillParticipant.drill_id == drill_id)
        .order_by(DrillParticipant.capacity, DrillParticipant.person_name))).scalars())


async def add_participants(
    db: AsyncSession, drill: Drill, people: list[dict]
) -> list[DrillParticipant]:
    existing = {(p.user_id, p.capacity) for p in await participants_for(db, drill.id)}
    added: list[DrillParticipant] = []
    for person in people:
        user_id = person.get("user_id")
        key = (uuid.UUID(str(user_id)) if user_id else None,
               person.get("capacity", "participant"))
        if key in existing and key[0] is not None:
            continue
        row = DrillParticipant(
            id=uuid.uuid4(), drill_id=drill.id,
            user_id=uuid.UUID(str(user_id)) if user_id else None,
            person_name=person["person_name"],
            capacity=person.get("capacity", "participant"),
            note=person.get("note"),
        )
        db.add(row)
        added.append(row)
        existing.add(key)
    await db.flush()
    return added


async def trend(db: AsyncSession, *, drill_type: str = "fire", limit: int = 8) -> list[dict]:
    """Evacuation times, oldest first.

    A single drill time means nothing. Three minutes ten is good or bad only
    against the last one, and "are we getting better" is the question asked
    every time.
    """
    rows = list((await db.execute(
        select(Drill).where(
            Drill.drill_type == drill_type,
            Drill.evacuation_seconds.is_not(None),
            Drill.held_on.is_not(None),
        ).order_by(Drill.held_on.desc()).limit(limit))).scalars())
    rows.reverse()
    return [{
        "drill_no": d.drill_no,
        "held_on": d.held_on.isoformat() if d.held_on else None,
        "seconds": d.evacuation_seconds,
        "announced": d.is_announced,
        "all_accounted_for": d.all_accounted_for,
    } for d in rows]


# ── Who can respond ──────────────────────────────────────────────────────────

async def responders(db: AsyncSession, *, today: date | None = None) -> list[dict]:
    """Qualified first aiders, computed from the training register.

    Not a maintained list. Who holds a current First Aid certificate is already
    recorded, and a second hand-kept roster would drift from it within a month
    — in the direction of listing somebody whose ticket expired, which is the
    worst way for this particular list to be wrong.
    """
    today = today or plant_today()

    course_id = (await db.execute(
        select(Course.id).where(Course.code == FIRST_AID_COURSE_CODE))).scalar()
    if course_id is None:
        return []

    # Latest record per person for that course.
    latest: dict[uuid.UUID, TrainingRecord] = {}
    for record in (await db.execute(
        select(TrainingRecord).where(TrainingRecord.course_id == course_id)
        .order_by(TrainingRecord.completed_on))).scalars():
        if record.user_id:
            latest[record.user_id] = record

    # The additional role is a second source: somebody may be a designated
    # first aider on a ticket earned before the register existed.
    role_holders = set((await db.execute(text(
        "SELECT ur.user_id FROM user_roles ur WHERE ur.role_code = 'first_aider'"
    ))).scalars().all())

    out: list[dict] = []
    for user_id in set(latest) | role_holders:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            continue
        profile = (await db.execute(
            select(WorkerProfile).where(WorkerProfile.user_id == user_id))).scalar_one_or_none()
        record = latest.get(user_id)
        expires = record.expires_on if record else None
        out.append({
            "user_id": str(user_id),
            "user_name": user.full_name,
            "shift_code": profile.shift_code if profile else None,
            "certified_on": record.completed_on.isoformat() if record else None,
            "expires_on": expires.isoformat() if expires else None,
            "is_current": bool(record) and (expires is None or expires >= today),
            "designated_only": user_id in role_holders and user_id not in latest,
        })
    out.sort(key=lambda r: (not r["is_current"], r["user_name"]))
    return out


async def coverage(db: AsyncSession, *, today: date | None = None) -> dict:
    """Is every shift covered — the question Reg 1101 actually asks.

    Reports "shift not recorded" as its own bucket rather than folding those
    people into a shift they might not work. A roster that quietly assumed
    days would report full coverage of a night shift that has none.
    """
    people = await responders(db, today=today)
    current = [r for r in people if r["is_current"]]

    by_shift: dict[str, int] = {}
    for r in current:
        key = r["shift_code"] or "__unknown__"
        by_shift[key] = by_shift.get(key, 0) + 1

    lapsed = [r for r in people if not r["is_current"]]
    return {
        "total": len(people),
        "current": len(current),
        "lapsed": [
            {"user_name": r["user_name"], "expires_on": r["expires_on"]}
            for r in lapsed
        ],
        "by_shift": [
            {"shift": "Shift not recorded" if k == "__unknown__" else k, "count": v}
            for k, v in sorted(by_shift.items())
        ],
        "shift_unknown": by_shift.get("__unknown__", 0),
    }
