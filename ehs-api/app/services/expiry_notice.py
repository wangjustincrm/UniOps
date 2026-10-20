"""The sweep behind the certificate expiry rules (models/expiry_notice.py).

Each tick, for each active rule: every certificate expiring between today and
today + the rule's days that the rule has not yet been told about goes out in
one email to the rule's address, and is logged so the next tick skips it.
One email per rule per tick rather than one per certificate: a new rule of
32 days on a plant with forty tickets would otherwise send forty.

Two kinds of certificate: a training record with an expiry date, and a
certification on a worker's profile (a licence). A training record replaced by
a later one of the same course is not reported — that person has renewed. Nor
is anything held by somebody no longer active.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.expiry_notice import CertExpiryNotice, CertExpiryRule
from app.models.mirrors import User
from app.models.training import TrainingRecord, WorkerCertification
from app.services import mail


@dataclass(frozen=True)
class Expiring:
    source_type: str
    source_id: uuid.UUID
    expires_on: date
    person: str
    what: str


def _still_here(user_id_col):
    """Held by nobody (a name only) or by somebody still active."""
    return or_(user_id_col.is_(None), exists().where(
        and_(User.id == user_id_col, User.is_active.is_(True))))


async def expiring(db: AsyncSession, *, today: date, until: date) -> list[Expiring]:
    later = aliased(TrainingRecord)
    records = (await db.execute(select(TrainingRecord).where(
        TrainingRecord.expires_on >= today, TrainingRecord.expires_on <= until,
        _still_here(TrainingRecord.user_id),
        ~exists().where(and_(
            later.user_id == TrainingRecord.user_id,
            later.course_id == TrainingRecord.course_id,
            later.completed_on > TrainingRecord.completed_on)),
    ))).scalars().all()
    certs = (await db.execute(select(WorkerCertification).where(
        WorkerCertification.expires_on >= today, WorkerCertification.expires_on <= until,
        _still_here(WorkerCertification.user_id),
    ))).scalars().all()
    out = [Expiring("training", r.id, r.expires_on, r.user_name, r.course_label)
           for r in records]
    out += [Expiring("cert", c.id, c.expires_on, c.user_name, c.cert_type_label)
            for c in certs]
    return sorted(out, key=lambda e: (e.expires_on, e.person.lower(), e.what.lower()))


def _body(rule: CertExpiryRule, due: list[Expiring], today: date) -> str:
    lines = [f"These certificates expire within {rule.days_before} days:", ""]
    for e in due:
        days = (e.expires_on - today).days
        left = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        lines.append(f"• {e.person} — {e.what} — expires {e.expires_on.isoformat()} ({left})")
    lines += ["", "Each certificate is listed once per rule. Rules are kept under "
              "Settings → Expiry notices."]
    return "\n".join(lines)


async def send_due(db: AsyncSession, *, today: date) -> int:
    """Send what is due. Returns how many emails were queued."""
    rules = (await db.execute(select(CertExpiryRule).where(
        CertExpiryRule.is_active.is_(True)))).scalars().all()
    if not rules:
        return 0
    candidates = await expiring(
        db, today=today, until=today + timedelta(days=max(r.days_before for r in rules)))
    if not candidates:
        return 0
    told = {(n.rule_id, n.source_type, n.source_id, n.expires_on) for n in (await db.execute(
        select(CertExpiryNotice).where(
            CertExpiryNotice.rule_id.in_([r.id for r in rules])))).scalars()}

    sent = 0
    for rule in rules:
        until = today + timedelta(days=rule.days_before)
        due = [e for e in candidates if e.expires_on <= until
               and (rule.id, e.source_type, e.source_id, e.expires_on) not in told]
        if not due:
            continue
        await mail.queue(
            db, to=[rule.email],
            subject=(f"{len(due)} certificate{'s' if len(due) != 1 else ''} "
                     f"expiring within {rule.days_before} days"),
            body=_body(rule, due, today),
            template_key="cert_expiry_notice",
            link=mail.module_link("/training?tab=records"),
            link_label="Open training",
        )
        db.add_all([CertExpiryNotice(
            id=uuid.uuid4(), rule_id=rule.id, source_type=e.source_type,
            source_id=e.source_id, expires_on=e.expires_on) for e in due])
        sent += 1
    await db.flush()
    return sent
