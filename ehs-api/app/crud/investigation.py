"""Investigation and cause persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import cause as cause_crud
from app.models.action import Action
from app.models.incident import Cause, Incident, IncidentInvestigation
from app.models.mirrors import User
from app.schemas.investigation import CausesReplace, InvestigationIn

# Actions in these states are no longer being worked, so a cause whose only
# action was cancelled counts as unaddressed again.
LIVE_ACTION_STATUSES = ("open", "in_progress", "pending_verification", "closed")


from app.schemas.investigation import as_whys  # noqa: E402


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

    # Editing un-signs it. The signature says "this is what I found"; leaving it
    # attached across a rewrite would let a signed investigation say something
    # its signatory never read — the same reason HSE's review is withdrawn when
    # the record changes under it.
    if inv.signature is not None:
        inv.signature = None
        inv.signed_by = None
        inv.signed_by_name = None
        inv.completed_at = None

    inv.investigator_id = payload.investigator_id
    inv.investigator_name = await _name(db, payload.investigator_id)
    inv.supporting_investigator_ids = [str(i) for i in payload.supporting_investigator_ids]
    inv.identified_hazard_ids = [str(i) for i in payload.identified_hazard_ids]
    inv.ppe_that_could_prevent = [str(i) for i in payload.ppe_that_could_prevent]
    inv.sequence_of_events = payload.sequence_of_events
    inv.root_cause_narrative = payload.root_cause_narrative
    whys = [{"question": w.question.strip(), "answer": w.answer.strip()}
            for w in payload.five_whys][:5]
    while whys and not (whys[-1]["question"] or whys[-1]["answer"]):
        whys.pop()
    inv.five_whys = whys
    await db.flush()
    return inv


FIVE_WHYS = 5


async def needs_five_whys(db: AsyncSession, incident: Incident) -> bool:
    """Round 12, Q8: a food-safety incident is investigated by the 5 Whys —
    the food-safety near miss, or anything in the Food Safety category."""
    if (incident.extra or {}).get("incident_type") == "near_miss_food_safety":
        return True
    if incident.category_id is None:
        return False
    from sqlalchemy import text
    code = (await db.execute(text(
        "select code from ehs_vocabulary_items where id = :id"),
        {"id": incident.category_id})).scalar()
    return code == "FOOD"


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
    if await needs_five_whys(db, incident):
        answered = [w for w in as_whys(inv.five_whys) if (w.get("answer") or "").strip()]
        if len(answered) < FIVE_WHYS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"A food safety investigation is signed off once all five whys are "
                f"answered; {len(answered)} of {FIVE_WHYS} are.")
    inv.signature = signature
    inv.signed_by = user_id
    inv.signed_by_name = await _name(db, user_id)
    inv.completed_at = now or datetime.now(timezone.utc)
    await db.flush()
    return inv


async def list_causes(db: AsyncSession, incident_id: uuid.UUID) -> list[Cause]:
    return await cause_crud.list_for(db, incident_id=incident_id)


async def replace_causes(
    db: AsyncSession, incident: Incident, payload: CausesReplace
) -> list[Cause]:
    """Replace the incident's causes with the given set.

    The rules — keeping a cause that has an action, raising an action with its
    cause, not raising it twice — are in `crud/cause.py`, because round 5 put the
    same apparatus on the hazard register and two copies of rules nobody can
    guess at is how they drift apart.

    What stays here is the one rule that is this screen's own: refused on a
    draft, for the same reason `upsert_investigation` refuses. The two are halves
    of one screen, and a rule that holds on one half and not the other lets
    causes be recorded against an incident nobody has reported yet.
    """
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Submit the incident before recording its causes",
        )
    return await cause_crud.replace(
        db,
        causes=payload.causes,
        source_type="incident",
        source_id=incident.id,
        source_ref=incident.incident_no,
        incident_id=incident.id,
    )


async def actions_by_cause(
    db: AsyncSession, incident_id: uuid.UUID
) -> dict[uuid.UUID, list[Action]]:
    return await cause_crud.actions_by_cause(
        db, source_type="incident", source_id=incident_id)
