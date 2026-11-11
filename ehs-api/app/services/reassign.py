"""Handing a leaver's open work to somebody else.

Round 10 (open box, q10-clear): "how does the hse role move items from a staff
member who has left the organization to a different staff member? this could
be a worker, team lead, supervisor or manager. hse role needs a way to review
and reassign items."

★ The rules, agreed in review with uniops-8b:
- Only what is still to be done moves, and only the field that says whose it
  is. Who submitted, signed, reported, completed, assessed or acknowledged
  anything is the record and is never touched; nor is anything closed.
- A recommendation's `sent_to` is who it was sent to: the new person is added,
  the leaver stays on it. Only the reply task moves.
- Each kind is checked: the new person must be active, not the leaver, and
  hold the key the kind needs. If anything fails nothing is written, and the
  failures are listed. HSE may leave a kind where it is.
- Tasks follow their item, matched on document and type, closed for the
  leaver and opened for the new person. The leaver's own tasks — read and
  sign, attend a course, their certificate — cannot move; they are closed and
  say why, and nothing is written that reads as done (no signature, no
  record). A clock on their own certificate closes as not required.
- An anonymous report's reporter is not a task and is never in here.
- Open permits are not moved: the requester is the legal record. They are
  listed, to close or reissue.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import Task, User
from app.services import tasks as task_service
from app.services.day_range import plant_today

# Tasks that are the leaver's own and cannot be anyone else's.
PERSONAL_TASKS = (task_service.TASK_READ_SIGN, task_service.TASK_COURSE_ATTEND,
                  task_service.TASK_CERT_EXPIRY)


@dataclass
class Item:
    id: uuid.UUID
    ref: str | None
    title: str
    path: str | None
    obj: object = field(repr=False, default=None)


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    # The key the new person must hold, if any (beyond being active).
    needs: str | None
    # (doc_type, how to find the documents its tasks hang off) — see _task_docs.
    doc_type: str | None


KINDS: tuple[Kind, ...] = (
    Kind("action", "Corrective actions they own", None, task_service.DOC_ACTION),
    Kind("investigation", "Investigations they lead", "ehs.incident.investigate",
         task_service.DOC_INCIDENT),
    Kind("course", "Training sessions they run", None, task_service.DOC_COURSE),
    Kind("jhsc", "JHSC recommendations awaiting their reply", None, task_service.DOC_JHSC),
    Kind("toolbox", "Toolbox talks they have to deliver", None, "ehs_tbt"),
    Kind("hazard", "Hazards they own", None, None),
    Kind("document", "Documents they own", "ehs.document.manage", task_service.DOC_DOCUMENT),
    Kind("asset", "Safety equipment they check", "ehs.asset.inspect", task_service.DOC_ASSET),
    Kind("schedule", "Scheduled forms assigned to them", None, task_service.DOC_RUN),
    Kind("area", "Areas they are responsible for", None, None),
)
BY_KEY = {k.key: k for k in KINDS}


async def collect(db: AsyncSession, leaver: uuid.UUID) -> dict[str, list[Item]]:
    """What is still to be done with the leaver's name on it, by kind."""
    from app.models.action import Action
    from app.models.area_owner import AreaOwner
    from app.models.asset import SafetyAsset
    from app.models.course import CourseDelivery
    from app.models.document import SafetyDocument
    from app.models.hazard import Hazard
    from app.models.incident import Incident, IncidentInvestigation
    from app.models.jhsc import JhscRecommendation
    from app.models.schedule import FormSchedule
    from app.models.toolbox import TbtDelivery, ToolboxTalk
    from app.crud.action import OPEN_STATUSES

    out: dict[str, list[Item]] = {}
    out["action"] = [Item(a.id, a.action_no, a.title, f"/actions/{a.id}", a)
                     for a in (await db.execute(select(Action).where(
                         Action.owner_id == leaver, Action.status.in_(OPEN_STATUSES))
                         .order_by(Action.due_date))).scalars()]
    out["investigation"] = [
        Item(inv.id, inc.incident_no, inc.title, f"/incidents/{inc.id}", inv)
        for inv, inc in (await db.execute(
            select(IncidentInvestigation, Incident)
            .join(Incident, Incident.id == IncidentInvestigation.incident_id)
            .where(IncidentInvestigation.investigator_id == leaver,
                   Incident.status.notin_(("closed", "draft"))))).all()]
    out["course"] = [Item(d.id, d.delivery_no, d.title, f"/training/sessions/{d.id}", d)
                     for d in (await db.execute(select(CourseDelivery).where(
                         CourseDelivery.coordinator_id == leaver,
                         CourseDelivery.status == "scheduled"))).scalars()]
    recs = (await db.execute(select(JhscRecommendation).where(
        JhscRecommendation.responded_at.is_(None),
        JhscRecommendation.sent_at.is_not(None)))).scalars().all()
    out["jhsc"] = [Item(r.id, f"#{r.seq}", r.recommendation[:120],
                        f"/jhsc/recommendations/{r.id}", r)
                   for r in recs
                   if any(str(p.get("id")) == str(leaver) for p in (r.sent_to or []))]
    out["toolbox"] = [
        Item(d.id, t.talk_no, t.title, f"/toolbox-talks?delivery={d.id}", d)
        for d, t in (await db.execute(
            select(TbtDelivery, ToolboxTalk).join(ToolboxTalk, ToolboxTalk.id == TbtDelivery.talk_id)
            .where(TbtDelivery.supervisor_id == leaver,
                   TbtDelivery.status == "pending"))).all()]
    out["hazard"] = [Item(h.id, h.hazard_no, h.title, f"/hazards/{h.id}", h)
                     for h in (await db.execute(select(Hazard).where(
                         Hazard.owner_id == leaver, Hazard.status == "open"))).scalars()]
    out["document"] = [Item(d.id, d.doc_no, d.title, f"/documents/{d.id}", d)
                       for d in (await db.execute(select(SafetyDocument).where(
                           SafetyDocument.owner_id == leaver,
                           SafetyDocument.status.notin_(("superseded", "retired", "archived")))))
                       .scalars()]
    out["asset"] = [Item(a.id, a.asset_no, a.name, f"/equipment?asset={a.id}", a)
                    for a in (await db.execute(select(SafetyAsset).where(
                        SafetyAsset.assigned_user_id == leaver,
                        SafetyAsset.status != "retired"))).scalars()]
    out["schedule"] = [Item(s.id, None, s.name, "/forms", s)
                       for s in (await db.execute(select(FormSchedule).where(
                           FormSchedule.assigned_user_id == leaver,
                           FormSchedule.is_active.is_(True)))).scalars()]
    out["area"] = [Item(a.id, None, a.location_path, "/settings/area-owners", a)
                   for a in (await db.execute(select(AreaOwner).where(
                       AreaOwner.user_id == leaver))).scalars()]
    return out


async def _task_docs(db: AsyncSession, kind: str, item: Item) -> list[uuid.UUID]:
    """The documents an item's tasks hang off: usually the item, but an
    asset's tasks are on its open inspections, and a schedule's on its rounds."""
    if kind == "investigation":
        return [item.obj.incident_id]
    if kind == "asset":
        from app.models.asset import AssetInspection
        return list((await db.execute(select(AssetInspection.id).where(
            AssetInspection.asset_id == item.id, AssetInspection.result.is_(None)))).scalars())
    if kind == "schedule":
        from app.models.schedule import FormScheduleRun
        return list((await db.execute(select(FormScheduleRun.id).where(
            FormScheduleRun.schedule_id == item.id,
            FormScheduleRun.status == "pending"))).scalars())
    return [item.id]


async def personal(db: AsyncSession, leaver: uuid.UUID) -> list[Task]:
    return list((await db.execute(select(Task).where(
        Task.assigned_user_id == leaver, Task.is_completed.is_(False),
        Task.type.in_(PERSONAL_TASKS)))).scalars())


async def open_permits(db: AsyncSession, leaver: uuid.UUID) -> list[Item]:
    from app.models.permit import Permit
    return [Item(p.id, p.permit_no, f"{p.type_label} — {p.work_description}", f"/permits/{p.id}")
            for p in (await db.execute(select(Permit).where(
                Permit.requested_by == leaver,
                Permit.status.in_(("draft", "issued", "suspended"))))).scalars()]


async def check(db: AsyncSession, leaver: uuid.UUID, plan: dict[str, uuid.UUID | None],
                found: dict[str, list[Item]]) -> list[str]:
    """Every reason the plan cannot be carried out — all of them, not the first."""
    from uniops_authz import has_permission
    problems = []
    for key, to in plan.items():
        kind = BY_KEY.get(key)
        if kind is None:
            problems.append(f"'{key}' is not something that can be handed over")
            continue
        if to is None or not found.get(key):
            continue
        person = await db.get(User, to)
        if person is None:
            problems.append(f"{kind.label}: no such person")
        elif person.id == leaver:
            problems.append(f"{kind.label}: they cannot be handed to the person leaving")
        elif not person.is_active:
            problems.append(f"{kind.label}: {person.full_name} has left too")
        elif kind.needs and not await has_permission(db, person.id, person.role or "", kind.needs):
            problems.append(f"{kind.label}: {person.full_name} may not take these on "
                            f"(needs {kind.needs})")
    return problems


async def _move_tasks(db, doc_type: str, doc_ids: list[uuid.UUID], leaver: uuid.UUID,
                      to: User, actor: uuid.UUID, now: datetime) -> int:
    moved = 0
    for doc_id in doc_ids:
        for t in (await db.execute(select(Task).where(
                Task.document_type == doc_type, Task.document_id == doc_id,
                Task.assigned_user_id == leaver, Task.is_completed.is_(False),
                Task.type.notin_(PERSONAL_TASKS)))).scalars().all():
            t.is_completed = True
            t.completed_at = now
            t.completed_by = actor
            t.description = (t.description or "") + f"\n\nHanded to {to.full_name}: the person it was assigned to left."
            await task_service.open_task(
                db, task_type=t.type, doc_type=t.document_type, doc_id=t.document_id,
                doc_number=t.document_number, title=t.title, assigned_role=t.assigned_role,
                assigned_user_id=to.id, description=t.description.split("\n\nHanded to")[0],
                priority=t.priority)
            moved += 1
    return moved


async def _write_history(db: AsyncSession, key: str, obj, leaver: User, to: User, *,
                         actor: uuid.UUID, actor_name: str | None, reason: str,
                         now: datetime) -> None:
    """★ Round 12, TC-13: the handover is written on each record moved.

    It was kept only on the handover itself (Settings → Reassign), so a record
    opened later said who has it and not how or why it got there. A corrective
    action gets a line in its own history, as a reassignment by hand does —
    which is also what the record it was raised on shows. Everything else gets
    a line in its change history beside the owner change the trail already
    records, with the reason, which that change alone does not carry.
    """
    from app.api.v1.actions import REASSIGNED
    from app.models.action import ActionUpdate
    from app.models.change_log import ChangeLogEntry
    from app.services.change_log import _row_ref

    if key == "action":
        db.add(ActionUpdate(
            id=uuid.uuid4(), action_id=obj.id, author_id=actor, author_name=actor_name,
            body=f"{REASSIGNED}{leaver.full_name} to {to.full_name}: {reason}",
            file_ids=[], new_status=None))
        return
    row_id = getattr(obj, "id", None)
    if row_id is None:
        return
    db.add(ChangeLogEntry(
        id=uuid.uuid4(), table_name=obj.__tablename__, row_id=str(row_id),
        row_ref=_row_ref(obj), action="update", field="handed over",
        old_value=leaver.full_name, new_value=f"{to.full_name} — {reason}"[:500],
        changed_by=actor, changed_by_name=actor_name, changed_at=now))


async def carry_out(db: AsyncSession, leaver: User, plan: dict[str, uuid.UUID | None],
                    *, actor: uuid.UUID, actor_name: str | None, reason: str) -> dict:
    """Check everything, then write everything, in the caller's transaction."""
    from app.models.statutory import StatutoryDeadline
    from app.models.training import WorkerCertification
    from app.services import deadlines as deadline_service

    found = await collect(db, leaver.id)
    problems = await check(db, leaver.id, plan, found)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"message": "Nothing was handed over.", "problems": problems})
    now = datetime.now(timezone.utc)
    moved: dict[str, dict] = {}
    for key, to_id in plan.items():
        items = found.get(key) or []
        if to_id is None or not items:
            continue
        to = await db.get(User, to_id)
        kind = BY_KEY[key]
        for it in items:
            o = it.obj
            if key == "action":
                o.owner_id, o.owner_name = to.id, to.full_name
            elif key == "investigation":
                o.investigator_id, o.investigator_name = to.id, to.full_name
            elif key == "course":
                o.coordinator_id, o.coordinator_name = to.id, to.full_name
            elif key == "jhsc":
                if not any(str(p.get("id")) == str(to.id) for p in o.sent_to or []):
                    o.sent_to = [*(o.sent_to or []), {"id": str(to.id), "name": to.full_name}]
            elif key == "toolbox":
                o.supervisor_id, o.supervisor_name = to.id, to.full_name
            elif key == "hazard":
                o.owner_id, o.owner_name = to.id, to.full_name
            elif key == "document":
                o.owner_id, o.owner_name = to.id, to.full_name
            elif key == "asset":
                o.assigned_user_id = to.id
            elif key == "schedule":
                o.assigned_user_id = to.id
            elif key == "area":
                from app.models.area_owner import AreaOwner
                clash = (await db.execute(select(AreaOwner.id).where(
                    AreaOwner.user_id == to.id, AreaOwner.location_id == o.location_id))).first()
                if clash:
                    await db.delete(o)
                else:
                    o.user_id, o.user_name = to.id, to.full_name
            if o not in db.deleted:
                await _write_history(db, key, o, leaver, to, actor=actor,
                                     actor_name=actor_name, reason=reason, now=now)
            if kind.doc_type:
                await _move_tasks(db, kind.doc_type, await _task_docs(db, key, it),
                                  leaver.id, to, actor, now)
        moved[key] = {"label": kind.label, "to_id": to.id, "to_name": to.full_name,
                      "to_email": to.email, "items": items}
        await db.flush()
        if key == "action":
            # ★ WHO-01 / TC-13: each through Who is told, as a reassignment by
            # hand is — the new owner, and the rest the rule names.
            from app.services import notify
            for it in items:
                await notify.action_assigned(db, it.obj, previous_owner_id=leaver.id,
                                             reason=reason, by_name=actor_name)

    # ★ Round 11 review: this page also serves somebody still here, such as a
    # supervisor handing work to a crew member. They keep their own tasks and
    # their certificate clocks; only somebody who has left loses them.
    closed = 0
    if not leaver.is_active:
        # Their own tasks: closed, saying why — never as done by them.
        for t in await personal(db, leaver.id):
            t.is_completed = True
            t.completed_at = now
            t.completed_by = actor
            t.description = (t.description or "") + (
                f"\n\nClosed by {actor_name or 'HSE'} on {plant_today():%-d %b %Y}: "
                f"{leaver.full_name} has left. Not done by them.")
            closed += 1
        # A clock on their own certificate: not required, not renewed.
        certs = list((await db.execute(select(WorkerCertification.id).where(
            WorkerCertification.user_id == leaver.id))).scalars())
        if certs:
            for d in (await db.execute(select(StatutoryDeadline).where(
                    StatutoryDeadline.source_type == "cert",
                    StatutoryDeadline.source_id.in_(certs),
                    StatutoryDeadline.satisfied_at.is_(None)))).scalars().all():
                await deadline_service.satisfy(
                    db, d, user_id=actor, user_name=actor_name,
                    evidence_note=f"Not required: employee left on {plant_today():%-d %b %Y}",
                    when=now)
    await db.flush()
    return {"moved": moved, "closed_own": closed}
