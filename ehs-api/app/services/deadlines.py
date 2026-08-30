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
    """Record that the obligation was discharged. Stops the clock."""
    deadline.satisfied_at = when
    deadline.satisfied_by = user_id
    deadline.satisfied_by_name = user_name
    deadline.evidence_file_id = evidence_file_id
    deadline.evidence_note = evidence_note
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
