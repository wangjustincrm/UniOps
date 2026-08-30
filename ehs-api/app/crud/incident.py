"""Incident persistence and the state changes that carry legal weight."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.incident import Incident, IncidentPerson
from app.models.mirrors import Location, User
from app.models.statutory import StatutoryDeadline
from app.schemas.incident import IncidentClassify, IncidentCreate
from app.services import deadlines as deadline_service
from app.services.numbering import next_number

# Editing is allowed while an incident is still a draft. Once submitted it is
# part of the record: corrections happen through the investigation and the
# audit trail, not by rewriting what was reported.
EDITABLE_STATUSES = ("draft",)


async def _resolve_name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _resolve_location_path(db: AsyncSession, location_id: uuid.UUID | None) -> str | None:
    """Snapshot the area path at write time.

    The plant tree gets renamed and restructured; a signed record has to keep
    reading the way it was signed. Reports still group by location_id, so a
    rename does not split a trend.
    """
    if location_id is None:
        return None
    return (await db.execute(select(Location.path).where(Location.id == location_id))).scalar()


async def create(
    db: AsyncSession, payload: IncidentCreate, *, reporter_id: uuid.UUID, now: datetime | None = None
) -> Incident:
    now = now or datetime.now(timezone.utc)
    incident = Incident(
        id=uuid.uuid4(),
        incident_no=await next_number(db, "incident", now=now),
        form_kind=payload.form_kind,
        title=payload.title,
        status="draft",
        occurred_at=payload.occurred_at,
        location_id=payload.location_id,
        location_path=await _resolve_location_path(db, payload.location_id),
        department_id=payload.department_id,
        shift_code=payload.shift_code,
        category_id=payload.category_id,
        description=payload.description,
        equipment_involved=payload.equipment_involved,
        witnesses_note=payload.witnesses_note,
        immediate_action_taken=payload.immediate_action_taken,
        reported_by=reporter_id,
        reported_by_name=await _resolve_name(db, reporter_id),
        is_anonymous=payload.is_anonymous,
        reported_to_id=payload.reported_to_id,
        reported_to_name=await _resolve_name(db, payload.reported_to_id),
        extra=payload.extra,
    )
    # An anonymous report keeps the reporter id for administrative recovery but
    # drops the name, which is what actually appears on screen and in exports.
    if payload.is_anonymous:
        incident.reported_by_name = None

    db.add(incident)
    await db.flush()

    for person in payload.persons:
        db.add(IncidentPerson(
            id=uuid.uuid4(), incident_id=incident.id, **person.model_dump()
        ))
    await db.flush()
    return incident


async def get(db: AsyncSession, incident_id: uuid.UUID) -> Incident:
    incident = (
        await db.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


async def load_persons(db: AsyncSession, incident_id: uuid.UUID) -> list[IncidentPerson]:
    return list((await db.execute(
        select(IncidentPerson).where(IncidentPerson.incident_id == incident_id)
    )).scalars())


async def load_deadlines(db: AsyncSession, incident_id: uuid.UUID) -> list[StatutoryDeadline]:
    return list((await db.execute(
        select(StatutoryDeadline)
        .where(StatutoryDeadline.source_type == "incident",
               StatutoryDeadline.source_id == incident_id)
        .order_by(StatutoryDeadline.due_at)
    )).scalars())


async def list_(
    db: AsyncSession,
    *,
    statuses: list[str] | None = None,
    form_kind: str | None = None,
    location_id: uuid.UUID | None = None,
    reported_by: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Incident]:
    stmt = select(Incident)
    if reported_by:
        stmt = stmt.where(Incident.reported_by == reported_by)
    if statuses:
        stmt = stmt.where(Incident.status.in_(statuses))
    if form_kind:
        stmt = stmt.where(Incident.form_kind == form_kind)
    if location_id:
        stmt = stmt.where(Incident.location_id == location_id)
    stmt = stmt.order_by(Incident.occurred_at.desc().nullslast()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def submit(db: AsyncSession, incident: Incident, *, now: datetime | None = None) -> Incident:
    if incident.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Incident {incident.incident_no} has already been submitted",
        )
    incident.status = "submitted"
    incident.submitted_at = now or datetime.now(timezone.utc)
    await db.flush()
    return incident


async def classify(
    db: AsyncSession,
    incident: Incident,
    payload: IncidentClassify,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[Incident, list[StatutoryDeadline]]:
    """Record the two classification facts and start whatever clocks they owe.

    Re-classifying is allowed — the first assessment at the scene is often
    revised once someone has seen a doctor, and an injury that turns out to be
    lost time must start its WSIB clock at that point. Deadlines already
    created are never removed: a clock that started is part of the record.
    """
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Submit the incident before classifying it",
        )

    incident.injury_class = payload.injury_class
    incident.mol_reportable = payload.mol_reportable
    incident.mol_reportable_reason = payload.mol_reportable_reason
    if payload.employer_aware_at is not None:
        incident.employer_aware_at = payload.employer_aware_at
    elif incident.employer_aware_at is None:
        # Nobody supplied it, so the moment of classification is the earliest
        # defensible answer to "when did the employer know".
        incident.employer_aware_at = now or datetime.now(timezone.utc)

    incident.classified_at = now or datetime.now(timezone.utc)
    incident.classified_by = user_id
    incident.classified_by_name = await _resolve_name(db, user_id)
    if incident.status == "submitted":
        incident.status = "under_investigation"
    await db.flush()

    created = await deadline_service.ensure_incident_deadlines(db, incident)
    return incident, created
