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

from app.crud.action import OPEN_STATUSES, create as create_action
from app.models.action import Action
from app.models.incident import Cause
from app.schemas.action import ActionCreate


def _owner_filter(*, incident_id: uuid.UUID | None, hazard_id: uuid.UUID | None):
    """The WHERE clause for one record's causes — and a refusal for anything else.

    ★★ Raised in review by uniops-41, and the failure it prevents is the worst
    one in this module. Without the check, a caller passing neither owner got
    `hazard_id == None`, which SQLAlchemy renders as `hazard_id IS NULL` — every
    incident's causes in the plant. `replace` then deletes every one of those that
    has no action attached. A forgotten argument would have silently wiped the
    root causes of every investigation on file.

    It is not hypothetical in the way it looks: this function was generalised
    precisely so that more callers would use it, and the next one is a job
    analysis. Both entry points go through here, so neither can be called wrong.
    """
    if (incident_id is None) == (hazard_id is None):
        raise ValueError(
            "A cause belongs to exactly one record: pass incident_id or hazard_id, "
            "not both and not neither")
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
    # Checked before anything is read, let alone deleted. `list_for` checks too,
    # but this is the function that deletes, and the guard belongs where a reader
    # of the deleting code can see it.
    _owner_filter(incident_id=incident_id, hazard_id=hazard_id)
    existing = await list_for(db, incident_id=incident_id, hazard_id=hazard_id)
    attached = {
        c_id for (c_id,) in (
            await db.execute(select(Action.cause_id).where(Action.cause_id.is_not(None)))
        ).all()
    }
    # ★ Matched on the entry first, the wording second: new snapshots are the
    # whole trail ("Physical › Slips"), and a cause saved as "Slips" with an
    # action on it would otherwise not match itself, be kept as a stray and
    # appear twice.
    keep_by_item = {c.vocabulary_item_id: c for c in existing
                    if c.id in attached and c.vocabulary_item_id}
    keep_by_label = {c.label: c for c in existing if c.id in attached}

    # ★ The wording each entry was recorded with. A save rewrites every row,
    # and an entry still chosen keeps that wording rather than today's
    # (review, uniops-8b); only a newly chosen one takes the list's.
    recorded = {c.vocabulary_item_id: c.label for c in existing if c.vocabulary_item_id}
    # ★ Review R8: and the department as it read when picked. Every save
    # rewrites the rows; a department not changed keeps the name recorded.
    recorded_dept = {c.completed_by_department_id: c.completed_by_department_name
                     for c in existing if c.completed_by_department_id}

    for cause in existing:
        if cause.id not in attached:
            await db.delete(cause)
    await db.flush()

    result: list[Cause] = []
    from app.crud.vocabulary import label_for
    for order, item in enumerate(causes):
        if item.vocabulary_item_id in recorded:
            item.label = recorded[item.vocabulary_item_id]
        elif item.vocabulary_item_id is not None:
            item.label = await label_for(
                db, "root_cause" if item.cause_type == "root" else "immediate_cause",
                item.vocabulary_item_id)
        kept = keep_by_item.pop(item.vocabulary_item_id, None) if item.vocabulary_item_id else None
        if kept is not None:
            keep_by_label.pop(kept.label, None)
        else:
            kept = keep_by_label.pop(item.label, None)
            if kept is not None and kept.vocabulary_item_id:
                keep_by_item.pop(kept.vocabulary_item_id, None)
        dept_id = getattr(item, "completed_by_department_id", None)
        done_by = ((dept_id, recorded_dept[dept_id]) if dept_id in recorded_dept
                   else await _department(db, dept_id))
        if kept is not None:
            kept.cause_type = item.cause_type
            kept.vocabulary_item_id = item.vocabulary_item_id
            kept.label = item.label
            kept.note = item.note
            kept.sort_order = order
            kept.completed_by_department_id, kept.completed_by_department_name = done_by
            kept.completed_on = getattr(item, "completed_on", None)
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
            completed_by_department_id=done_by[0],
            completed_by_department_name=done_by[1],
            completed_on=getattr(item, "completed_on", None),
        )
        db.add(cause)
        result.append(cause)

    # Anything still here had an action attached but was dropped from the new
    # set. Keep it, so the action still points at something explaining itself.
    result.extend({c.id: c for c in [*keep_by_label.values(), *keep_by_item.values()]}.values())
    await db.flush()

    # ★ Round 11: "when editing the investigation, adding corrective actions are
    # not saved." This skipped any cause that already had an action, so a second
    # one was dropped with a 200. A root cause can carry several; what must not
    # happen is the same one twice, because the screen saves the whole set. Only
    # a live one counts: one already closed does not stop the work being raised
    # again.
    raised = {
        (c_id, title.strip().casefold()) for c_id, title in (await db.execute(
            select(Action.cause_id, Action.title).where(
                Action.cause_id.in_([c.id for c in result]),
                Action.status.in_(OPEN_STATUSES))
        )).all()
    } if result else set()
    for cause, item in zip(result, causes, strict=False):
        if getattr(item, "action", None) is None:
            continue
        key = (cause.id, item.action.title.strip().casefold())
        if key in raised:
            continue
        raised.add(key)
        await create_action(db, ActionCreate(
            source_type=source_type,
            source_id=source_id,
            cause_id=cause.id,
            title=item.action.title,
            description=item.action.description,
            owner_id=item.action.owner_id,
            due_date=item.action.due_date,
            hierarchy_of_control=item.action.hierarchy_of_control,
            priority=item.action.priority,
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


async def _department(db, department_id) -> tuple:
    """(id, name as it reads now) — kept on the cause, as every reference on a
    signed record is. An id that names no department is refused, not stored."""
    if department_id is None:
        return None, None
    from fastapi import HTTPException, status as http
    from app.models.mirrors import Department
    dept = await db.get(Department, department_id)
    if dept is None or not dept.is_active:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY, "That department is not on the list")
    return dept.id, dept.name
