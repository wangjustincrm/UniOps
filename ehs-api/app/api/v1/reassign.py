"""HSE hands a leaver's open work to others (round 10). The rules are in
services/reassign — read its docstring before changing them."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings
from app.models.mirrors import User
from app.models.reassignment import Reassignment
from app.services import mail, reassign

router = APIRouter(prefix="/reassign")


class ItemOut(BaseModel):
    id: uuid.UUID
    ref: str | None
    title: str
    path: str | None


class GroupOut(BaseModel):
    key: str
    label: str
    needs: str | None
    items: list[ItemOut]


class PreviewOut(BaseModel):
    leaver_id: uuid.UUID
    leaver_name: str
    is_active: bool
    groups: list[GroupOut]
    own_tasks: dict[str, int]
    open_permits: list[ItemOut]


def _item(i: reassign.Item) -> ItemOut:
    return ItemOut(id=i.id, ref=i.ref, title=i.title, path=i.path)


@router.get("/{user_id}", response_model=PreviewOut)
async def preview(user_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """What is still to be done with this person's name on it, by kind."""
    leaver = await db.get(User, user_id)
    if leaver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")
    found = await reassign.collect(db, leaver.id)
    own: dict[str, int] = {}
    for t in await reassign.personal(db, leaver.id):
        own[t.type] = own.get(t.type, 0) + 1
    return PreviewOut(
        leaver_id=leaver.id, leaver_name=leaver.full_name, is_active=leaver.is_active,
        groups=[GroupOut(key=k.key, label=k.label, needs=k.needs,
                         items=[_item(i) for i in found[k.key]])
                for k in reassign.KINDS if found.get(k.key)],
        own_tasks=own,
        open_permits=[_item(i) for i in await reassign.open_permits(db, leaver.id)])


class PlanIn(BaseModel):
    # kind → the person it goes to; a kind left out (or null) stays where it is.
    to: dict[str, uuid.UUID | None]
    reason: str = Field(min_length=5, max_length=1000)


class DoneOut(BaseModel):
    id: uuid.UUID
    summary: dict


async def _reply_due(db, rec_id: uuid.UUID):
    from app.models.statutory import StatutoryDeadline
    return (await db.execute(select(StatutoryDeadline.due_at).where(
        StatutoryDeadline.source_type == "jhsc_rec", StatutoryDeadline.source_id == rec_id,
        StatutoryDeadline.satisfied_at.is_(None)))).scalar()


@router.post("/{user_id}", response_model=DoneOut)
async def carry_out(user_id: uuid.UUID, payload: PlanIn, db: SessionDep, user: CanManageSettings):
    """Check the whole plan, then hand everything over in one go — or nothing."""
    from app.services import statutory
    leaver = await db.get(User, user_id)
    if leaver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")
    me = await db.get(User, _user_id(user))
    result = await reassign.carry_out(
        db, leaver, payload.to, actor=_user_id(user),
        actor_name=me.full_name if me else None, reason=payload.reason.strip())

    summary = {"own_tasks_closed": result["closed_own"], "moved": {
        k: {"label": v["label"], "to_id": str(v["to_id"]), "to_name": v["to_name"],
            "count": len(v["items"]), "refs": [i.ref or i.title for i in v["items"]]}
        for k, v in result["moved"].items()}}
    record = Reassignment(id=uuid.uuid4(), leaver_id=leaver.id, leaver_name=leaver.full_name,
                          reason=payload.reason.strip(), done_at=datetime.now(timezone.utc),
                          done_by=_user_id(user), done_by_name=me.full_name if me else None,
                          summary=summary)
    db.add(record)
    await db.flush()

    # One email to each person something went to; a JHSC recommendation is
    # its own email, filed on the recommendation, with its words and the
    # time left — the written reply is the employer's duty and the new
    # recipient may never have seen it (review, uniops-8b).
    by_person: dict[str, list[dict]] = {}
    for key, v in result["moved"].items():
        if key == "jhsc":
            for it in v["items"]:
                due = await _reply_due(db, it.id)
                await mail.queue(
                    db, to=[v["to_email"]],
                    subject=f"JHSC recommendation {it.ref} now awaits your reply",
                    body="\n".join([
                        f"{leaver.full_name} has left, and HSE has asked you to reply to this "
                        "JHSC recommendation in their place.", "",
                        it.obj.recommendation, "",
                        (f"The written reply is due {due.astimezone(statutory.ONTARIO):%-d %b %Y %H:%M} "
                         "(OHSA s.9(20), 21 days from when it was sent)." if due else
                         "OHSA s.9(20) asks for a written reply within 21 days of it being sent."),
                        "", f"Why: {payload.reason.strip()}"]),
                    doc_type="ehs_jhsc", doc_id=it.id, template_key="jhsc_reassigned",
                    link=mail.module_link(it.path), link_label="Open the recommendation")
            continue
        by_person.setdefault(v["to_email"], []).append(v)
    for email, groups in by_person.items():
        lines = [f"{leaver.full_name} has left, and HSE ({me.full_name if me else 'Safety'}) "
                 "has handed you their open work in Safety:", ""]
        for g in groups:
            lines.append(f"{g['label']} — {len(g['items'])}")
            lines += [f"  • {i.ref + ' ' if i.ref else ''}{i.title}" for i in g["items"][:25]]
            if len(g["items"]) > 25:
                lines.append(f"  … and {len(g['items']) - 25} more")
            lines.append("")
        lines.append(f"Why: {payload.reason.strip()}")
        await mail.queue(db, to=[email], subject=f"You now have {leaver.full_name}'s open work in Safety",
                         body="\n".join(lines), template_key="reassigned",
                         link=mail.module_link("/actions/mine"), link_label="Open My Actions")
    return DoneOut(id=record.id, summary=summary)
