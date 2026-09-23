"""First-aid register endpoints — Regulation 1101."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident, CanWriteFirstAid
from app.crud import first_aid as crud
from app.crud import incident as incident_crud
from app.schemas.first_aid import EscalateIn, FirstAidIn, FirstAidOut
from app.schemas.incident import DeadlineOut, IncidentOut, IncidentPersonOut

router = APIRouter()


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


@router.post("", response_model=FirstAidOut, status_code=status.HTTP_201_CREATED)
async def record_treatment(payload: FirstAidIn, db: SessionDep, user: CanWriteFirstAid):  # noqa: ARG001
    """Closed. First aid is recorded on the injured-worker form.

    Two places recording the same event meant the same cut finger was entered
    twice, or once in whichever the person happened to open. Refused rather
    than removed, so a caller still pointed here is told where to go instead of
    getting a 404 and guessing.
    """
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "First aid is now recorded on the injury form: report an incident and "
        "set its type to First Aid. The entries already in this register stay "
        "and are still listed here.",
    )


@router.get("", response_model=list[FirstAidOut])
async def list_register(
    db: SessionDep,
    user: CanReadIncident,  # noqa: ARG001
    injured_user_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    escalated: bool | None = Query(
        default=None,
        description="Entries that did (true) or did not (false) become incidents."),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """The Regulation 1101 register: both what was entered here and what has
    been recorded on an injury form since.

    ★ Reading only this table would have made the statutory register stop
    being complete at the moment first aid moved onto the incident form — a
    compliance regression caused by a usability fix. The older entries keep
    their own numbers and dates; they were deliberately not rewritten into
    incidents, because renumbering twelve statutory records to tidy a screen
    is not a trade worth making.
    """
    rows = await crud.list_(db, injured_user_id=injured_user_id, location_id=location_id,
                            escalated=escalated, limit=limit, offset=offset)
    entries = [FirstAidOut.model_validate(r) for r in rows]
    entries.extend(await crud.first_aid_from_incidents(
        db, injured_user_id=injured_user_id, location_id=location_id, limit=limit))
    entries.sort(key=lambda e: e.occurred_at, reverse=True)
    return entries[:limit]


@router.get("/{entry_id}", response_model=FirstAidOut)
async def get_entry(entry_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    return FirstAidOut.model_validate(await crud.get(db, entry_id))


@router.post("/{entry_id}/escalate", response_model=IncidentOut,
             status_code=status.HTTP_201_CREATED)
async def escalate(
    entry_id: uuid.UUID, payload: EscalateIn, db: SessionDep, user: CanWriteFirstAid,
):
    """Raise an incident from a treatment that turned out to be more than first aid.

    The new incident inherits the entry's time and place — the injury happened
    when it happened, not when someone realised how serious it was.
    """
    entry = await crud.get(db, entry_id)
    incident = await crud.escalate(db, entry, payload, reporter_id=_user_id(user))
    # Load the child rows the same way the incident endpoints do — returning a
    # bare IncidentOut would answer with an empty persons list, and the caller
    # would reasonably read that as "nobody was hurt".
    out = IncidentOut.model_validate(incident)
    out.persons = [IncidentPersonOut.model_validate(p)
                   for p in await incident_crud.load_persons(db, incident.id)]
    out.deadlines = [DeadlineOut.model_validate(d)
                     for d in await incident_crud.load_deadlines(db, incident.id)]
    return out
