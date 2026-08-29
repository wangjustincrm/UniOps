"""Incident endpoints.

Reporting is open to every authenticated employee — `ehs.incident.report` is
granted to every role — because the whole point of the module is that the
person who saw it can file it. Everything after reporting is gated more
narrowly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.permissions import (
    CanClassifyIncident,
    CanReadIncident,
    CanReportIncident,
)
from app.crud import incident as crud
from app.schemas.incident import (
    IncidentClassify,
    IncidentCreate,
    IncidentListItem,
    IncidentOut,
)

router = APIRouter()


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


async def _to_out(db, incident) -> IncidentOut:
    out = IncidentOut.model_validate(incident)
    out.persons = [  # type: ignore[assignment]
        p for p in await crud.load_persons(db, incident.id)
    ]
    out.deadlines = [  # type: ignore[assignment]
        d for d in await crud.load_deadlines(db, incident.id)
    ]
    return IncidentOut.model_validate(out.model_dump())


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def create_incident(payload: IncidentCreate, db: SessionDep, user: CanReportIncident):
    incident = await crud.create(db, payload, reporter_id=_user_id(user))
    return await _to_out(db, incident)


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    db: SessionDep,
    user: CanReadIncident,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
    form_kind: str | None = None,
    location_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    rows = await crud.list_(
        db, statuses=status_in, form_kind=form_kind,
        location_id=location_id, limit=limit, offset=offset,
    )
    return [IncidentListItem.model_validate(r) for r in rows]


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    return await _to_out(db, await crud.get(db, incident_id))


@router.post("/{incident_id}/submit", response_model=IncidentOut)
async def submit_incident(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    incident = await crud.get(db, incident_id)
    await crud.submit(db, incident)
    return await _to_out(db, incident)


@router.post("/{incident_id}/classify", response_model=IncidentOut)
async def classify_incident(
    incident_id: uuid.UUID,
    payload: IncidentClassify,
    db: SessionDep,
    user: CanClassifyIncident,
):
    """Set the injury class and the Ministry-reportable flag, and start the
    statutory clocks those two facts owe.

    The response carries the deadlines that now exist, so the caller can show
    the countdown immediately rather than re-fetching.
    """
    incident = await crud.get(db, incident_id)
    await crud.classify(db, incident, payload, user_id=_user_id(user),
                        now=datetime.now(timezone.utc))
    return await _to_out(db, incident)
