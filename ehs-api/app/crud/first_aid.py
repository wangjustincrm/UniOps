"""First-aid register persistence.

Regulation 1101 asks for a standing record of every treatment given, whether or
not it ever becomes a reportable incident — and most never do. It is therefore
its own table rather than a flavour of incident, and it is written once: in
production a trigger refuses updates and deletes, because a treatment record
that can be edited afterwards is not evidence of anything.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.first_aid import FirstAidLog
from app.models.incident import Incident, IncidentPerson
from app.models.mirrors import Location, User
from app.schemas.first_aid import EscalateIn, FirstAidIn
from app.services.numbering import next_number


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _location_path(db: AsyncSession, location_id: uuid.UUID | None) -> str | None:
    if location_id is None:
        return None
    return (await db.execute(select(Location.path).where(Location.id == location_id))).scalar()


async def create(
    db: AsyncSession, payload: FirstAidIn, *, now: datetime | None = None
) -> FirstAidLog:
    entry = FirstAidLog(
        id=uuid.uuid4(),
        log_no=await next_number(db, "first_aid", now=now or datetime.now(timezone.utc)),
        occurred_at=payload.occurred_at,
        location_id=payload.location_id,
        location_path=await _location_path(db, payload.location_id),
        injured_user_id=payload.injured_user_id,
        injured_name=payload.injured_name,
        first_aider_id=payload.first_aider_id,
        first_aider_name=await _name(db, payload.first_aider_id),
        body_part_id=payload.body_part_id,
        body_part_label=payload.body_part_label,
        treatment_given=payload.treatment_given,
        sent_offsite=payload.sent_offsite,
        follow_up=payload.follow_up,
        file_ids=[str(f) for f in payload.file_ids],
    )
    db.add(entry)
    await db.flush()
    return entry


async def get(db: AsyncSession, entry_id: uuid.UUID) -> FirstAidLog:
    entry = (await db.execute(
        select(FirstAidLog).where(FirstAidLog.id == entry_id)
    )).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "First-aid entry not found")
    return entry


async def list_(
    db: AsyncSession,
    *,
    injured_user_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    escalated: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FirstAidLog]:
    stmt = select(FirstAidLog)
    if injured_user_id:
        stmt = stmt.where(FirstAidLog.injured_user_id == injured_user_id)
    if location_id:
        stmt = stmt.where(FirstAidLog.location_id == location_id)
    if escalated is True:
        stmt = stmt.where(FirstAidLog.incident_id.is_not(None))
    elif escalated is False:
        stmt = stmt.where(FirstAidLog.incident_id.is_(None))
    stmt = stmt.order_by(FirstAidLog.occurred_at.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def escalate(
    db: AsyncSession,
    entry: FirstAidLog,
    payload: EscalateIn,
    *,
    reporter_id: uuid.UUID,
    now: datetime | None = None,
) -> Incident:
    """Raise an incident from a first-aid entry and link the two.

    The incident inherits the entry's time and place rather than taking
    today's: the injury happened when it happened, and the MOL clock — if this
    later turns out to be a critical injury — runs from the occurrence.

    The register entry is not modified beyond the link. It stays the
    contemporaneous record of what was done at the time.
    """
    if entry.incident_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{entry.log_no} has already been escalated",
        )
    now = now or datetime.now(timezone.utc)
    incident = Incident(
        id=uuid.uuid4(),
        incident_no=await next_number(db, "incident", now=now),
        form_kind="medical",
        title=payload.title,
        status="submitted",
        submitted_at=now,
        occurred_at=entry.occurred_at,
        employer_aware_at=payload.employer_aware_at or now,
        location_id=entry.location_id,
        location_path=entry.location_path,
        description=payload.description or entry.treatment_given,
        reported_by=reporter_id,
        reported_by_name=await _name(db, reporter_id),
    )
    db.add(incident)
    await db.flush()

    db.add(IncidentPerson(
        id=uuid.uuid4(),
        incident_id=incident.id,
        role="injured",
        user_id=entry.injured_user_id,
        person_name=entry.injured_name,
        body_part_id=entry.body_part_id,
        body_part_label=entry.body_part_label,
        treatment=entry.treatment_given,
    ))
    entry.incident_id = incident.id
    await db.flush()
    return incident
