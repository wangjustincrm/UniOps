"""HSE's review of a record once every corrective action on it is closed.

Round 12, Q5 (INC-06): "when all capa's are verified and closed on all forms
that have a capa, put a task on the HSE role 'my actions' to review the form.
allow the task to be clicked on and open the record."

The record is whatever raised the actions — an incident, an inspection form, a
hazard, a JHSC recommendation, a drill, a permit, a piece of safety equipment.
Actions are grouped by their source, as `crud.cause.list_for_source` groups
them; a JHSC action's source is the meeting, so it is reviewed as the
recommendation that owns it.

The task is raised once, when the last open action on the record closes. It is
done when HSE presses Reviewed on it, or — for an incident — when HSE closes
the incident, which is the review. A further action raised afterwards and then
closed raises it again, because there is then something new to review.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.action import OPEN_STATUSES
from app.models.action import Action
from app.models.mirrors import Task
from app.services import tasks as task_service

REVIEWER_ROLE = "ehs_manager"


@dataclass(frozen=True)
class Source:
    doc_type: str
    doc_id: uuid.UUID
    number: str
    noun: str


async def _source(db: AsyncSession, action: Action) -> Source | None:
    st, sid = action.source_type, action.source_id
    if sid is None:
        return None
    if st == "incident":
        from app.models.incident import Incident
        row = await db.get(Incident, sid)
        return row and Source(task_service.DOC_INCIDENT, row.id, row.incident_no, "incident")
    if st == "inspection":
        from app.models.form import FormSubmission
        row = await db.get(FormSubmission, sid)
        return row and Source(task_service.DOC_SUBMISSION, row.id, row.submission_no, "form")
    if st == "hazard":
        from app.models.hazard import Hazard
        row = await db.get(Hazard, sid)
        return row and Source(task_service.DOC_HAZARD, row.id, row.hazard_no, "hazard assessment")
    if st == "drill":
        from app.models.drill import Drill
        row = await db.get(Drill, sid)
        return row and Source(task_service.DOC_DRILL, row.id, row.drill_no, "drill")
    if st == "permit":
        from app.models.permit import Permit
        row = await db.get(Permit, sid)
        return row and Source(task_service.DOC_PERMIT, row.id, row.permit_no, "work permit")
    if st == "asset":
        from app.models.asset import SafetyAsset
        row = await db.get(SafetyAsset, sid)
        return row and Source(task_service.DOC_ASSET, row.id, row.asset_no, "safety equipment")
    if st == "jhsc":
        from app.models.jhsc import JhscMeeting, JhscRecommendation
        rec = (await db.execute(select(JhscRecommendation).where(
            JhscRecommendation.action_id == action.id))).scalar_one_or_none()
        meeting = await db.get(JhscMeeting, rec.meeting_id if rec else sid)
        if rec is not None:
            number = f"{meeting.meeting_no}-{rec.seq}" if meeting else f"Recommendation {rec.seq}"
            return Source(task_service.DOC_JHSC, rec.id, number, "JHSC recommendation")
        return None
    return None


async def _siblings_open(db: AsyncSession, action: Action) -> bool:
    """Whether anything raised from the same record is still open."""
    if action.source_type == "jhsc":
        # One action per recommendation: nothing else shares it.
        return False
    return (await db.execute(select(Action.id).where(
        Action.source_type == action.source_type, Action.source_id == action.source_id,
        Action.id != action.id, Action.status.in_(OPEN_STATUSES)).limit(1))).first() is not None


async def after_action_closed(db: AsyncSession, action: Action) -> Task | None:
    """Raise HSE's review when this was the last open action on its record."""
    if await _siblings_open(db, action):
        return None
    src = await _source(db, action)
    if src is None:
        return None
    already = (await db.execute(select(Task.id).where(
        Task.type == task_service.TASK_REVIEW_CAPAS, Task.document_type == src.doc_type,
        Task.document_id == src.doc_id, Task.is_completed.is_(False)).limit(1))).first()
    if already:
        return None
    return await task_service.open_task(
        db, task_type=task_service.TASK_REVIEW_CAPAS, doc_type=src.doc_type,
        doc_id=src.doc_id, doc_number=src.number,
        title=f"Review {src.number}: every corrective action is closed",
        assigned_role=REVIEWER_ROLE,
        description=f"All the corrective actions on this {src.noun} have been verified and "
                    "closed. Open it and review it.")


async def after_action_raised(db: AsyncSession, action: Action) -> None:
    """★ Review L3: a review asked for while nothing was open is withdrawn when
    another action is raised on the record — its title said every action was
    closed. It is asked for again when that one closes."""
    src = await _source(db, action)
    if src is None:
        return
    await task_service.close_tasks_for(
        db, doc_type=src.doc_type, doc_id=src.doc_id, task_type=task_service.TASK_REVIEW_CAPAS,
        completed_by=None,
        note=f"Withdrawn: {action.action_no} was raised on it. The review is asked for again "
             "when that is closed.")
    await db.flush()


async def close_for(db: AsyncSession, doc_type: str, doc_id: uuid.UUID,
                    user_id: uuid.UUID | None) -> None:
    await task_service.close_tasks_for(
        db, doc_type=doc_type, doc_id=doc_id, task_type=task_service.TASK_REVIEW_CAPAS,
        completed_by=user_id)
