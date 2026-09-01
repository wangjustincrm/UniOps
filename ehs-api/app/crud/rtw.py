"""Return-to-work planning after an injury.

The plan is what turns "they were hurt" into "here is what they can and cannot
do, and when we will look again". Three things depend on it existing:

* the **WSIB Form 7 package**, which asks for the modified duties and whether
  they are at regular pay — until now those fields came out blank on every
  export because nothing could write this table;
* the **lost-time classification**, since HSE's own rule is that modified
  duties do not count as lost time, and that judgement needs the plan to point
  at;
* the **check-ins**, which are the only evidence that anybody looked again.

`at_regular_pay` is deliberately nullable rather than defaulting to true. HSE's
boundary cases turn on it, and "nobody has said" is a different answer from
"yes" — recording a guess as a fact is how a WSIB submission goes out wrong.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident, IncidentPerson
from app.models.mirrors import User
from app.models.rtw import RtwCheckin, RtwPlan


async def plans_for(db: AsyncSession, incident_id: uuid.UUID) -> list[RtwPlan]:
    return list((await db.execute(
        select(RtwPlan).where(RtwPlan.incident_id == incident_id)
        .order_by(RtwPlan.start_date))).scalars())


async def get_plan(db: AsyncSession, plan_id: uuid.UUID) -> RtwPlan:
    plan = await db.get(RtwPlan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Return-to-work plan not found")
    return plan


async def create_plan(db: AsyncSession, incident: Incident, payload: dict) -> RtwPlan:
    """Open a plan for whoever was hurt.

    The worker defaults to the injured person already on the incident rather
    than being asked for again — they are recorded there, and asking twice is
    how the two end up disagreeing.
    """
    worker_id = payload.get("worker_user_id")
    worker_name = payload.get("worker_name")

    if worker_id is None or not worker_name:
        injured = (await db.execute(
            select(IncidentPerson).where(
                IncidentPerson.incident_id == incident.id,
                IncidentPerson.role == "injured",
            ).order_by(IncidentPerson.created_at))).scalars().first()
        if injured is None and worker_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Nobody is recorded as injured on this incident, so there is no "
                "one to plan a return for. Add the injured person first.")
        worker_id = worker_id or (injured.user_id if injured else None)
        worker_name = worker_name or (injured.person_name if injured else None)

    if not worker_name and worker_id:
        worker_name = (await db.execute(
            select(User.full_name).where(User.id == worker_id))).scalar()

    plan = RtwPlan(
        id=uuid.uuid4(),
        incident_id=incident.id,
        worker_user_id=worker_id,
        worker_name=worker_name or "Unknown",
        functional_abilities=payload.get("functional_abilities") or {},
        restrictions=payload.get("restrictions"),
        modified_duties=payload.get("modified_duties"),
        at_regular_pay=payload.get("at_regular_pay"),
        start_date=payload.get("start_date") or date.today(),
        expected_end_date=payload.get("expected_end_date"),
    )
    db.add(plan)
    await db.flush()
    return plan


async def update_plan(db: AsyncSession, plan: RtwPlan, fields: dict) -> RtwPlan:
    for key, value in fields.items():
        setattr(plan, key, value)
    await db.flush()
    return plan


async def close_plan(
    db: AsyncSession, plan: RtwPlan, *, on: date | None = None, outcome: str = "completed"
) -> RtwPlan:
    """The worker is back on full duties, or the plan ended some other way."""
    plan.actual_end_date = on or date.today()
    plan.status = outcome
    await db.flush()
    return plan


async def checkins_for(db: AsyncSession, plan_id: uuid.UUID) -> list[RtwCheckin]:
    return list((await db.execute(
        select(RtwCheckin).where(RtwCheckin.plan_id == plan_id)
        .order_by(RtwCheckin.checkin_date))).scalars())


async def add_checkin(
    db: AsyncSession, plan: RtwPlan, *, notes: str, on: date | None,
    by_user_id: uuid.UUID | None,
) -> RtwCheckin:
    checkin = RtwCheckin(
        id=uuid.uuid4(), plan_id=plan.id,
        checkin_date=on or date.today(),
        notes=notes, by_user_id=by_user_id,
        # Name snapshot, like every other users FK in this module: the
        # check-in is evidence somebody looked, and it has to keep saying who
        # even after they leave.
        by_name=(await db.execute(
            select(User.full_name).where(User.id == by_user_id))).scalar()
        if by_user_id else None,
    )
    db.add(checkin)
    await db.flush()
    return checkin
