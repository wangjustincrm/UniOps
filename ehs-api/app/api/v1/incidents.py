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
    CanManageStatutory,
    CanReadIncident,
    CanReportIncident,
)
from app.crud import incident as crud
from app.crud import investigation as inv_crud
from app.schemas.incident import (
    IncidentClassify,
    IncidentCreate,
    IncidentListItem,
    IncidentOut,
)
from app.schemas.wsib import Form7FiledIn, Form7Package
from app.schemas.investigation import (
    CauseActionOut,
    CauseOut,
    CausesReplace,
    CauseTreeOut,
    InvestigationIn,
    InvestigationOut,
    InvestigationSignIn,
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


# ── Investigation and causes ────────────────────────────────────────────────


@router.get("/{incident_id}/investigation", response_model=InvestigationOut | None)
async def get_investigation(incident_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    await crud.get(db, incident_id)
    inv = await inv_crud.get_investigation(db, incident_id)
    return InvestigationOut.model_validate(inv) if inv else None


@router.put("/{incident_id}/investigation", response_model=InvestigationOut)
async def upsert_investigation(
    incident_id: uuid.UUID, payload: InvestigationIn, db: SessionDep, user: CanClassifyIncident,  # noqa: ARG001
):
    incident = await crud.get(db, incident_id)
    inv = await inv_crud.upsert_investigation(db, incident, payload)
    return InvestigationOut.model_validate(inv)


@router.post("/{incident_id}/investigation/sign", response_model=InvestigationOut)
async def sign_investigation(
    incident_id: uuid.UUID, payload: InvestigationSignIn, db: SessionDep, user: CanClassifyIncident,
):
    incident = await crud.get(db, incident_id)
    inv = await inv_crud.sign_investigation(
        db, incident, payload.signature, user_id=_user_id(user),
        now=datetime.now(timezone.utc))
    return InvestigationOut.model_validate(inv)


@router.put("/{incident_id}/causes", response_model=CauseTreeOut)
async def replace_causes(
    incident_id: uuid.UUID, payload: CausesReplace, db: SessionDep, user: CanClassifyIncident,  # noqa: ARG001
):
    incident = await crud.get(db, incident_id)
    await inv_crud.replace_causes(db, incident, payload)
    return await _cause_tree(db, incident)


@router.get("/{incident_id}/cause-tree", response_model=CauseTreeOut)
async def cause_tree(incident_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Causes with the corrective actions hanging off each one.

    A root cause with nothing under it is flagged rather than left to the
    reader to notice — that gap is exactly what an audit looks for.
    """
    return await _cause_tree(db, await crud.get(db, incident_id))


async def _cause_tree(db, incident) -> CauseTreeOut:
    causes = await inv_crud.list_causes(db, incident.id)
    grouped = await inv_crud.actions_by_cause(db, incident.id)

    def _to_out(cause) -> CauseOut:
        actions = grouped.get(cause.id, [])
        out = CauseOut.model_validate(cause)
        out.actions = [CauseActionOut.model_validate(a) for a in actions]
        # Only root causes are expected to have actions; an immediate cause is
        # a description of what happened, not something to fix.
        out.needs_action = cause.cause_type == "root" and not actions
        return out

    immediate = [_to_out(c) for c in causes if c.cause_type == "immediate"]
    root = [_to_out(c) for c in causes if c.cause_type == "root"]
    return CauseTreeOut(
        incident_id=incident.id,
        incident_no=incident.incident_no,
        immediate=immediate,
        root=root,
        unaddressed_root_causes=sum(1 for c in root if c.needs_action),
    )


# ── WSIB Form 7 ─────────────────────────────────────────────────────────────


@router.get("/{incident_id}/wsib-form7", response_model=Form7Package)
async def wsib_form7(incident_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Everything needed to fill in Form 7 in one sitting.

    UniOps does not file it — statutory submission goes through WSIB's own
    service. What this returns is the fields we hold plus the ones we do not,
    so whoever files knows what to gather before they start.
    """
    from app.services.wsib import build_package
    return await build_package(db, await crud.get(db, incident_id))


@router.post("/{incident_id}/wsib-form7/filed", response_model=Form7Package)
async def wsib_form7_filed(
    incident_id: uuid.UUID, payload: Form7FiledIn, db: SessionDep, user: CanManageStatutory,
):
    """Record that Form 7 was filed, which is what stops the clock.

    The confirmation number WSIB returns is the evidence the obligation was
    discharged, so it is required rather than optional — a deadline marked
    satisfied with nothing behind it is worse than one still showing as open.
    """
    from app.services import deadlines as deadline_service
    from app.services.wsib import build_package

    incident = await crud.get(db, incident_id)
    deadline = next(
        (d for d in await crud.load_deadlines(db, incident.id) if d.kind == "wsib_form7"),
        None,
    )
    if deadline is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.incident_no} owes WSIB nothing — it has not been classified "
            "as a medical-aid or lost-time injury",
        )
    if deadline.satisfied_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Form 7 for {incident.incident_no} was already recorded as filed on "
            f"{deadline.satisfied_at.date().isoformat()}",
        )

    await deadline_service.satisfy(
        db, deadline,
        user_id=_user_id(user),
        user_name=None,
        evidence_file_id=payload.evidence_file_id,
        evidence_note=payload.confirmation_number,
        when=payload.filed_at or datetime.now(timezone.utc),
    )
    return await build_package(db, incident)
