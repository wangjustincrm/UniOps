"""What is new to one person: the counts behind the menu flags.

Round 12, ACT-01/02/03/04 and INC-08 — see app/models/seen.py for why "seen"
is per person and why there is a baseline.

One function computes everything the flags show, and My Actions reads the same
answer for its "New" tile and chips, so the number in the menu, the tile and
the chips on the cards cannot disagree.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback
from app.models.incident import Incident
from app.models.mirrors import Task
from app.models.news import NewsItem
from app.models.seen import SeenBaseline, SeenMark
from app.services import area_scope, notify
from app.services import tasks as task_service

KIND_TASK = "task"
KIND_INCIDENT = "incident"
KIND_FEEDBACK = "feedback"


async def baseline(db: AsyncSession, user_id: uuid.UUID) -> SeenBaseline:
    """This person's baseline, written the first time it is asked for."""
    row = await db.get(SeenBaseline, user_id)
    if row is None:
        now = datetime.now(timezone.utc)
        await db.execute(insert(SeenBaseline).values(
            user_id=user_id, since=now, news_seen_at=now).on_conflict_do_nothing())
        await db.flush()
        row = await db.get(SeenBaseline, user_id)
    return row


async def mark(db: AsyncSession, user_id: uuid.UUID, kind: str, ids: list[uuid.UUID]) -> None:
    """These have been opened. Opening again moves the time on, which matters
    for feedback: a second reply after the first was read is new again."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return
    await baseline(db, user_id)
    now = datetime.now(timezone.utc)
    stmt = insert(SeenMark).values(
        [{"user_id": user_id, "kind": kind, "item_id": i, "seen_at": now} for i in ids])
    await db.execute(stmt.on_conflict_do_update(
        index_elements=["user_id", "kind", "item_id"], set_={"seen_at": now}))
    await db.flush()


async def _marks(db: AsyncSession, user_id: uuid.UUID, kind: str,
                 ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    if not ids:
        return {}
    return dict((await db.execute(select(SeenMark.item_id, SeenMark.seen_at).where(
        SeenMark.user_id == user_id, SeenMark.kind == kind,
        SeenMark.item_id.in_(ids)))).all())


@dataclass
class NewIncident:
    id: uuid.UUID
    incident_no: str
    title: str
    form_kind: str
    submitted_at: datetime
    location_path: str | None


@dataclass
class Summary:
    task_ids: list[uuid.UUID] = field(default_factory=list)
    # The corrective actions among those tasks — what the cards are keyed on.
    action_ids: list[uuid.UUID] = field(default_factory=list)
    news: int = 0
    incidents: list[NewIncident] = field(default_factory=list)
    feedback_answered: int = 0

    @property
    def my_actions(self) -> int:
        """The flag on My Actions: new work, new news and new incidents."""
        return len(self.task_ids) + self.news + len(self.incidents)


async def incidents_to_flag(db: AsyncSession, user_id: uuid.UUID, roles: set[str],
                            since: datetime) -> list[NewIncident]:
    """INC-08 / D12: incidents sent since the baseline, near misses included,
    for HSE (every one) and for whoever is down for the area it happened in.
    Never one's own report."""
    site_wide = bool(roles & area_scope.SITE_WIDE_ROLES)
    areas = [] if site_wide else await area_scope.areas_for(db, user_id)
    if not site_wide and not areas:
        return []
    rows = (await db.execute(text(
        "select i.id, i.incident_no, i.title, i.form_kind, i.submitted_at, l.path"
        "  from ehs_incidents i left join locations l on l.id = i.location_id"
        " where i.submitted_at > :since and i.status <> 'draft'"
        "   and (i.reported_by is null or i.reported_by <> :me)"
        " order by i.submitted_at desc limit 500"),
        {"since": since, "me": user_id})).all()
    if not site_wide:
        rows = [r for r in rows if area_scope.covers(areas, r[5])]
    seen = await _marks(db, user_id, KIND_INCIDENT, [r[0] for r in rows])
    return [NewIncident(id=r[0], incident_no=r[1], title=r[2], form_kind=r[3],
                        submitted_at=r[4], location_path=r[5])
            for r in rows if r[0] not in seen]


async def summary(db: AsyncSession, user: dict, user_id: uuid.UUID) -> Summary:
    from app.api.v1.actions import assigned_task_rows

    base = await baseline(db, user_id)
    out = Summary()

    tasks = [t for t in await assigned_task_rows(db, user, with_owned_actions=True)
             if t.created_at and t.created_at > base.since]
    seen = await _marks(db, user_id, KIND_TASK, [t.id for t in tasks])
    fresh = [t for t in tasks if t.id not in seen]
    out.task_ids = [t.id for t in fresh]
    out.action_ids = list(dict.fromkeys(
        t.document_id for t in fresh
        if t.document_type == task_service.DOC_ACTION and t.type == task_service.TASK_DO_ACTION))

    out.news = len((await db.execute(select(NewsItem.id).where(
        NewsItem.archived_at.is_(None), NewsItem.posted_at > base.news_seen_at,
        (NewsItem.posted_by.is_(None)) | (NewsItem.posted_by != user_id)))).all())

    roles = await notify.roles_held_by(db, user_id, user.get("role", ""))
    out.incidents = await incidents_to_flag(db, user_id, roles, base.since)

    answered = (await db.execute(select(Feedback.id, Feedback.answered_at).where(
        Feedback.submitted_by == user_id, Feedback.answered_at.is_not(None),
        Feedback.answered_at > base.since))).all()
    marks = await _marks(db, user_id, KIND_FEEDBACK, [r[0] for r in answered])
    out.feedback_answered = sum(1 for fid, at in answered if fid not in marks or marks[fid] < at)
    return out


async def opened_document(db: AsyncSession, user: dict, user_id: uuid.UUID,
                          doc_type: str, doc_id: uuid.UUID) -> None:
    """Somebody opened a record: whatever on it was new to them is not now.

    Their own tasks on it, and the incident itself where it was flagged. A
    task given to a role is marked for this person only — the next supervisor
    still sees it as new.
    """
    from app.api.v1.actions import assigned_task_rows

    rows = await assigned_task_rows(db, user, with_owned_actions=True)
    await mark(db, user_id, KIND_TASK,
               [t.id for t in rows if t.document_type == doc_type and t.document_id == doc_id])
    if doc_type == task_service.DOC_INCIDENT:
        incident = await db.get(Incident, doc_id)
        if incident is not None:
            await mark(db, user_id, KIND_INCIDENT, [incident.id])


async def opened_task(db: AsyncSession, user_id: uuid.UUID, task: Task) -> None:
    await mark(db, user_id, KIND_TASK, [task.id])


async def read_news(db: AsyncSession, user_id: uuid.UUID) -> None:
    row = await baseline(db, user_id)
    row.news_seen_at = datetime.now(timezone.utc)
    await db.flush()


async def read_feedback(db: AsyncSession, user_id: uuid.UUID) -> None:
    ids = list((await db.execute(select(Feedback.id).where(
        Feedback.submitted_by == user_id, Feedback.answered_at.is_not(None)))).scalars())
    await mark(db, user_id, KIND_FEEDBACK, ids)
