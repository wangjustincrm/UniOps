"""Investigation and cause persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action import Action
from app.models.incident import Incident, IncidentCause, IncidentInvestigation
from app.models.mirrors import User
from app.schemas.investigation import CausesReplace, InvestigationIn

# Actions in these states are no longer being worked, so a cause whose only
# action was cancelled counts as unaddressed again.
LIVE_ACTION_STATUSES = ("open", "in_progress", "pending_verification", "closed")


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def get_investigation(
    db: AsyncSession, incident_id: uuid.UUID
) -> IncidentInvestigation | None:
    return (await db.execute(
        select(IncidentInvestigation).where(IncidentInvestigation.incident_id == incident_id)
    )).scalar_one_or_none()


async def upsert_investigation(
    db: AsyncSession, incident: Incident, payload: InvestigationIn
) -> IncidentInvestigation:
    """Create or update the investigation. Idempotent by incident.

    Refused while the incident is a draft: there is nothing to investigate
    until it has been reported.
    """
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Submit the incident before opening an investigation",
        )
    inv = await get_investigation(db, incident.id)
    if inv is None:
        inv = IncidentInvestigation(id=uuid.uuid4(), incident_id=incident.id)
        db.add(inv)

    inv.investigator_id = payload.investigator_id
    inv.investigator_name = await _name(db, payload.investigator_id)
    inv.supporting_investigator_ids = [str(i) for i in payload.supporting_investigator_ids]
    inv.identified_hazard_ids = [str(i) for i in payload.identified_hazard_ids]
    inv.ppe_that_could_prevent = [str(i) for i in payload.ppe_that_could_prevent]
    inv.sequence_of_events = payload.sequence_of_events
    inv.root_cause_narrative = payload.root_cause_narrative
    await db.flush()
    return inv


async def sign_investigation(
    db: AsyncSession,
    incident: Incident,
    signature: str,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> IncidentInvestigation:
    inv = await get_investigation(db, incident.id)
    if inv is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "There is no investigation to sign yet")
    inv.signature = signature
    inv.signed_by = user_id
    inv.signed_by_name = await _name(db, user_id)
    inv.completed_at = now or datetime.now(timezone.utc)
    await db.flush()
    return inv


async def list_causes(db: AsyncSession, incident_id: uuid.UUID) -> list[IncidentCause]:
    return list((await db.execute(
        select(IncidentCause)
        .where(IncidentCause.incident_id == incident_id)
        .order_by(IncidentCause.cause_type, IncidentCause.sort_order)
    )).scalars())


async def replace_causes(
    db: AsyncSession, incident: Incident, payload: CausesReplace
) -> list[IncidentCause]:
    """Replace the incident's causes with the given set.

    A cause that already has a corrective action attached is kept rather than
    deleted, matched on its label — dropping it would orphan the action and
    lose the link between a root cause and what is being done about it.
    """
    existing = await list_causes(db, incident.id)
    attached = {
        c_id for (c_id,) in (
            await db.execute(select(Action.cause_id).where(Action.cause_id.is_not(None)))
        ).all()
    }
    keep_by_label = {c.label: c for c in existing if c.id in attached}

    for cause in existing:
        if cause.id not in attached:
            await db.delete(cause)
    await db.flush()

    result: list[IncidentCause] = []
    for order, item in enumerate(payload.causes):
        kept = keep_by_label.pop(item.label, None)
        if kept is not None:
            kept.cause_type = item.cause_type
            kept.vocabulary_item_id = item.vocabulary_item_id
            kept.note = item.note
            kept.sort_order = order
            result.append(kept)
            continue
        cause = IncidentCause(
            id=uuid.uuid4(),
            incident_id=incident.id,
            cause_type=item.cause_type,
            vocabulary_item_id=item.vocabulary_item_id,
            label=item.label,
            note=item.note,
            sort_order=order,
        )
        db.add(cause)
        result.append(cause)

    # Anything still here had an action attached but was dropped from the new
    # set. Keep it, so the action still points at something explaining itself.
    result.extend(keep_by_label.values())
    await db.flush()
    return result


async def actions_by_cause(
    db: AsyncSession, incident_id: uuid.UUID
) -> dict[uuid.UUID, list[Action]]:
    rows = (await db.execute(
        select(Action)
        .where(Action.source_type == "incident", Action.source_id == incident_id)
        .order_by(Action.due_date)
    )).scalars()
    grouped: dict[uuid.UUID, list[Action]] = {}
    for action in rows:
        if action.cause_id is None:
            continue
        grouped.setdefault(action.cause_id, []).append(action)
    return grouped
