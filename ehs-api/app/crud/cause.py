"""Immediate and root causes, for whatever found them.

Round 5, answer box 11 put causes on the hazard register as well as on an
incident investigation. The rules are the same in both places and none of them
is obvious, so they live here once rather than in two copies that drift:

  · a cause with a corrective action attached is kept rather than deleted, even
    when it is dropped from the new set — deleting it would orphan the action and
    lose the link between a root cause and what is being done about it;
  · an action can be raised with its cause, in the same request, because the
    client cannot do it itself: an action points at a cause and a cause has no id
    until it is saved;
  · and only where that cause has no action yet, because the screen saves the
    whole set every time and a payload still carrying last time's action must not
    raise it twice.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.action import create as create_action
from app.models.action import Action
from app.models.incident import Cause
from app.schemas.action import ActionCreate


def _owner_filter(*, incident_id: uuid.UUID | None, hazard_id: uuid.UUID | None):
    if incident_id is not None:
        return Cause.incident_id == incident_id
    return Cause.hazard_id == hazard_id


async def list_for(
    db: AsyncSession, *,
    incident_id: uuid.UUID | None = None,
    hazard_id: uuid.UUID | None = None,
) -> list[Cause]:
    return list((await db.execute(
        select(Cause)
        .where(_owner_filter(incident_id=incident_id, hazard_id=hazard_id))
        .order_by(Cause.cause_type, Cause.sort_order)
    )).scalars())


async def replace(
    db: AsyncSession, *,
    causes,
    source_type: str,
    source_id: uuid.UUID,
    source_ref: str | None,
    incident_id: uuid.UUID | None = None,
    hazard_id: uuid.UUID | None = None,
) -> list[Cause]:
    """Replace one record's causes with the given set.

    `source_type` and `source_ref` are what any action raised here is filed
    against; `incident_id` / `hazard_id` is which column owns the cause row. They
    are separate because an action's `source_type` is a wider vocabulary than the
    two columns a cause can hang off.
    """
    existing = await list_for(db, incident_id=incident_id, hazard_id=hazard_id)
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

    result: list[Cause] = []
    for order, item in enumerate(causes):
        kept = keep_by_label.pop(item.label, None)
        if kept is not None:
            kept.cause_type = item.cause_type
            kept.vocabulary_item_id = item.vocabulary_item_id
            kept.note = item.note
            kept.sort_order = order
            result.append(kept)
            continue
        cause = Cause(
            id=uuid.uuid4(),
            incident_id=incident_id,
            hazard_id=hazard_id,
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

    for cause, item in zip(result, causes, strict=False):
        if getattr(item, "action", None) is None or cause.id in attached:
            continue
        await create_action(db, ActionCreate(
            source_type=source_type,
            source_id=source_id,
            cause_id=cause.id,
            title=item.action.title,
            description=item.action.description,
            owner_id=item.action.owner_id,
            due_date=item.action.due_date,
            hierarchy_of_control=item.action.hierarchy_of_control,
        ), source_ref=source_ref)
    await db.flush()
    return result


async def actions_by_cause(
    db: AsyncSession, *, source_type: str, source_id: uuid.UUID
) -> dict[uuid.UUID, list[Action]]:
    """The actions raised against each cause of one record.

    What lets a screen show a root cause with its actions underneath — and a root
    cause with nothing under it read as visibly incomplete, which is what an
    auditor looks for.
    """
    rows = (await db.execute(
        select(Action)
        .where(Action.source_type == source_type, Action.source_id == source_id)
        .order_by(Action.due_date)
    )).scalars()
    grouped: dict[uuid.UUID, list[Action]] = {}
    for action in rows:
        if action.cause_id is None:
            continue
        grouped.setdefault(action.cause_id, []).append(action)
    return grouped
