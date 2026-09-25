"""Starting and satisfying statutory clocks.

The arithmetic lives in app/services/statutory.py as pure functions; this
module is the part that touches the database — reading the holiday list,
writing the deadline row, and recording that an obligation was discharged.

Which clocks an incident starts is decided in one place, `clocks_for_incident`,
because it is the rule most likely to be got wrong and the one an auditor is
most likely to ask about. Both clocks can run on the same incident: a lost-time
injury that is also a critical injury owes WSIB a Form 7 and the Ministry a
written report, on different deadlines measured from different instants.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import Holiday
from app.models.incident import Incident
from app.models.statutory import StatutoryDeadline
from app.models.training import WorkerCertification
from app.services import statutory
from app.services import tasks as task_service

# Which document a deadline's inbox task hangs off. The same map the sweep in
# `tasks/scheduler.py` uses; kept here too because closing a task needs to find
# it, and importing the scheduler from a service it calls would be a cycle.
DEADLINE_DOC_TYPE = {
    "incident": task_service.DOC_INCIDENT,
    "cert": task_service.DOC_CERT,
    "jhsc_rec": task_service.DOC_JHSC,
    "document": task_service.DOC_DOCUMENT,
    "sds": "ehs_sds",
}


def task_lead(kind: str) -> str:
    """How an obligation's task title begins.

    ★ This string is how the sweep finds a task it has already raised, and how
    satisfying an obligation finds the task to close. One record can owe two
    obligations — a critical injury owes both the Ministry and WSIB — so the
    document alone does not identify the task, and the `tasks` mirror has no
    column to hold a deadline id. Both callers must derive it here: change the
    wording in one place only and the two stop finding each other's work.
    """
    return f"{kind.replace('_', ' ')} — "


async def load_holidays(db: AsyncSession) -> frozenset[date]:
    rows = (await db.execute(select(Holiday.holiday_date))).scalars().all()
    return frozenset(rows)


def clocks_for_incident(incident: Incident) -> list[tuple[str, datetime]]:
    """The statutory clocks this incident starts, as (kind, starts_at).

    MOL runs from the occurrence, per OHSA s.51(1) — the wording is "within
    forty-eight hours after the occurrence", not after the employer found out.

    WSIB runs from employer awareness, which is a different instant and is
    usually later. An incident that has been classified as a reportable injury
    but has no awareness timestamp falls back to the occurrence: better a
    deadline that is too early than a legal clock that never starts.
    """
    clocks: list[tuple[str, datetime]] = []

    if incident.mol_reportable and incident.occurred_at:
        clocks.append(("mol_48h", incident.occurred_at))

    # First aid alone is not reportable to WSIB; medical aid and lost time are.
    if incident.injury_class in ("medical_aid", "lost_time"):
        starts = incident.employer_aware_at or incident.occurred_at
        if starts:
            clocks.append(("wsib_form7", starts))

    return clocks


async def ensure_incident_deadlines(
    db: AsyncSession, incident: Incident, *, holidays: frozenset[date] | None = None
) -> list[StatutoryDeadline]:
    """Create any statutory deadline this incident owes and does not yet have.

    Idempotent: re-classifying an incident does not duplicate its clocks, and
    an already-satisfied obligation is never recreated. Deadlines are not
    removed if a later re-classification makes them inapplicable — a clock that
    started is part of the record, and an HSE Manager cancels it explicitly.
    """
    holidays = holidays if holidays is not None else await load_holidays(db)

    existing = {
        d.kind: d
        for d in (
            await db.execute(
                select(StatutoryDeadline).where(
                    StatutoryDeadline.source_type == "incident",
                    StatutoryDeadline.source_id == incident.id,
                )
            )
        ).scalars()
    }

    created: list[StatutoryDeadline] = []
    for kind, starts_at in clocks_for_incident(incident):
        if kind in existing:
            continue
        clock_type, authority = statutory.RULES[kind]
        deadline = StatutoryDeadline(
            id=uuid.uuid4(),
            source_type="incident",
            source_id=incident.id,
            source_ref=incident.incident_no,
            kind=kind,
            regulation_ref=authority,
            clock_type=clock_type,
            starts_at=starts_at,
            due_at=statutory.compute_due(kind, starts_at, holidays),
        )
        db.add(deadline)
        created.append(deadline)

    if created:
        await db.flush()
    return created


async def satisfy(
    db: AsyncSession,
    deadline: StatutoryDeadline,
    *,
    user_id: uuid.UUID | None,
    user_name: str | None,
    evidence_file_id: uuid.UUID | None = None,
    evidence_note: str | None = None,
    when: datetime,
) -> StatutoryDeadline:
    """Record that the obligation was discharged. Stops the clock, and the
    prompt.

    ★ Closing the task is half of discharging the obligation. It was not done:
    HSE could file the report with the Ministry, upload the receipt, and still
    be told to do it — for good, because the sweep only ever raised these and
    nothing ever closed one. Nobody saw it while tasks assigned to a role
    reached no screen; once they did, this was most of what was on the page.

    Only this obligation's task. A critical injury owes the Ministry a report
    and WSIB a Form 7, and filing one of them does not discharge the other.
    """
    deadline.satisfied_at = when
    deadline.satisfied_by = user_id
    deadline.satisfied_by_name = user_name
    deadline.evidence_file_id = evidence_file_id
    deadline.evidence_note = evidence_note

    doc_type = DEADLINE_DOC_TYPE.get(deadline.source_type, task_service.DOC_INCIDENT)
    lead = task_lead(deadline.kind)
    for task in await task_service.open_tasks_for(
        db, doc_type=doc_type, doc_id=deadline.source_id
    ):
        if task.type == task_service.TASK_STATUTORY and (task.title or "").startswith(lead):
            task.is_completed = True
            task.completed_at = when
            task.completed_by = user_id

    await db.flush()
    return deadline


async def ensure_certification_deadline(
    db: AsyncSession, cert: WorkerCertification
) -> StatutoryDeadline | None:
    """Put a certification's expiry on the same clock as everything else.

    Certificate expiries need no scanner of their own: they become rows in
    ehs_statutory_deadlines, so the sweep that chases the Ministry's 48 hours
    chases these too, they escalate the same way, and they appear on the same
    compliance calendar. That reuse is the reason the table was built to hold
    every kind of clock rather than just the two statutory ones.

    The deadline is the expiry date itself. Nothing is created for a
    certification with no expiry — it does not lapse.
    """
    if cert.expires_on is None:
        return None

    existing = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "cert",
            StatutoryDeadline.source_id == cert.id,
        )
    )).scalar_one_or_none()

    # End of the expiry day, in Ontario terms — a licence is valid all day.
    due_at = datetime.combine(
        cert.expires_on, time(23, 59, 59), tzinfo=statutory.ONTARIO,
    )
    if existing is not None:
        # Renewing pushes the date out; the row is reused so its history stays.
        if existing.due_at != due_at:
            existing.due_at = due_at
            existing.escalation_level = 0
            await db.flush()
        return existing

    deadline = StatutoryDeadline(
        id=uuid.uuid4(),
        source_type="cert",
        source_id=cert.id,
        source_ref=cert.cert_type_label,
        kind="cert_expiry",
        regulation_ref=None,
        clock_type="calendar",
        starts_at=datetime.combine(
            cert.issued_on or cert.expires_on, time(0, 0), tzinfo=statutory.ONTARIO),
        due_at=due_at,
    )
    db.add(deadline)
    await db.flush()
    return deadline


async def ensure_jhsc_reply_deadline(
    db: AsyncSession, recommendation, *, meeting_ref: str
) -> StatutoryDeadline:
    """OHSA s.9(20): twenty-one days to respond in writing.

    The clock starts when the recommendation is recorded, not when the meeting
    was held. That is the conservative reading — a minute book written up three
    weeks late would otherwise arrive with its deadline already expired, and
    the module would be reporting a breach it caused itself.
    """
    existing = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "jhsc_rec",
            StatutoryDeadline.source_id == recommendation.id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    starts_at = recommendation.created_at or datetime.now(statutory.ONTARIO)
    clock_type, authority = statutory.RULES["jhsc_reply_21d"]
    deadline = StatutoryDeadline(
        id=uuid.uuid4(),
        source_type="jhsc_rec",
        source_id=recommendation.id,
        source_ref=f"{meeting_ref} #{recommendation.seq}",
        kind="jhsc_reply_21d",
        regulation_ref=authority,
        clock_type=clock_type,
        starts_at=starts_at,
        due_at=statutory.compute_due("jhsc_reply_21d", starts_at),
    )
    db.add(deadline)
    await db.flush()
    return deadline


async def ensure_document_review_deadline(db: AsyncSession, document) -> StatutoryDeadline | None:
    """The annual policy review, OHSA s.25(2)(j), on the same clock as the rest.

    Nothing is created for a document with no review date — a form template
    does not lapse. The row is reused when the date moves so that a reviewed
    and re-published policy keeps one history rather than accumulating one
    deadline per year.
    """
    if document.review_due_on is None:
        return None

    due_at = datetime.combine(
        document.review_due_on, time(23, 59, 59), tzinfo=statutory.ONTARIO)

    existing = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "document",
            StatutoryDeadline.source_id == document.id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        if existing.due_at != due_at:
            existing.due_at = due_at
            existing.satisfied_at = None
            existing.escalation_level = 0
            await db.flush()
        return existing

    _, authority = statutory.RULES["policy_annual"]
    deadline = StatutoryDeadline(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_ref=document.doc_no,
        kind="policy_annual",
        regulation_ref=authority if document.category == "policy" else None,
        clock_type="calendar",
        starts_at=datetime.combine(
            document.effective_on or document.review_due_on, time(0, 0),
            tzinfo=statutory.ONTARIO),
        due_at=due_at,
    )
    db.add(deadline)
    await db.flush()
    return deadline
