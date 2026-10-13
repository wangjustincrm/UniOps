"""Incident persistence and the state changes that carry legal weight."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import case, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.incident import Incident, IncidentPerson
from app.models.mirrors import Location, User
from app.models.statutory import StatutoryDeadline
from app.services import incident_type
from app.schemas.incident import IncidentClassify, IncidentCreate
from app.services import deadlines as deadline_service
from app.services.numbering import next_number
from app.services.day_range import day_bounds

# Editing is allowed while an incident is still a draft. Once submitted it is
# part of the record: corrections happen through the investigation and the
# audit trail, not by rewriting what was reported.
EDITABLE_STATUSES = ("draft",)


async def _resolve_name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _resolve_job_title(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    """The person's current job title, or None.

    Reads the EHS position tables rather than `users.role`: the latter holds
    system access levels, and printing "requester" as a job title under a
    signature on a statutory record would be wrong in a way nobody would catch.
    Returns None until HR populates positions — an empty role prints nothing.
    """
    if user_id is None:
        return None
    return (await db.execute(text("""
        select p.name from ehs_worker_positions wp
          join ehs_job_positions p on p.id = wp.position_id
         where wp.user_id = :u
           and (wp.effective_to is null or wp.effective_to >= current_date)
         order by wp.effective_from desc limit 1
    """), {"u": str(user_id)})).scalar()


async def _resolve_category(
    db: AsyncSession, code: str
) -> tuple[uuid.UUID | None, str | None]:
    """Category id plus a label snapshot, so a later rename leaves history alone."""
    row = (await db.execute(text(
        "select id, label from ehs_vocabulary_items "
        " where vocabulary_code = 'incident_category' and code = :c"
    ), {"c": code})).first()
    return (row[0], row[1]) if row else (None, None)


async def _resolve_location_path(db: AsyncSession, location_id: uuid.UUID | None) -> str | None:
    """Snapshot the area path at write time.

    The plant tree gets renamed and restructured; a signed record has to keep
    reading the way it was signed. Reports still group by location_id, so a
    rename does not split a trend.
    """
    if location_id is None:
        return None
    return (await db.execute(select(Location.path).where(Location.id == location_id))).scalar()


async def create(
    db: AsyncSession, payload: IncidentCreate, *, reporter_id: uuid.UUID, now: datetime | None = None
) -> Incident:
    now = now or datetime.now(timezone.utc)
    incident = Incident(
        id=uuid.uuid4(),
        incident_no=await next_number(db, "incident", now=now),
        form_kind=payload.form_kind,
        title=payload.title,
        status="draft",
        occurred_at=payload.occurred_at,
        location_id=payload.location_id,
        location_path=await _resolve_location_path(db, payload.location_id),
        department_id=payload.department_id,
        shift_code=payload.shift_code,
        category_id=payload.category_id,
        description=payload.description,
        equipment_involved=payload.equipment_involved,
        witnesses_note=payload.witnesses_note,
        immediate_action_taken=payload.immediate_action_taken,
        reported_by=reporter_id,
        reported_by_name=await _resolve_name(db, reporter_id),
        is_anonymous=payload.is_anonymous,
        reported_to_id=payload.reported_to_id,
        reported_to_name=await _resolve_name(db, payload.reported_to_id),
        signature=payload.signature,
        extra=payload.extra,
    )

    # HSE's twelve-option incident type fans out into the three fields that
    # drive the clocks and the rates. An explicit injury_class from the form
    # wins: Fatality and Unconscious are MOL-reportable and say nothing about
    # lost time, so the form asks for that separately.
    # HSE: name, role and timestamp fill themselves in when the form is signed.
    if payload.signature:
        incident.signer_name = incident.reported_by_name or await _resolve_name(db, reporter_id)
        incident.signer_role = await _resolve_job_title(db, reporter_id)
        incident.signed_at = now

    if payload.incident_type:
        t = incident_type.BY_CODE.get(payload.incident_type)
        if t is None:
            raise ValueError(f"Unknown incident type '{payload.incident_type}'")
        # ★ Three outcomes, not two: a class the reporter chose, an explicit
        # "nobody was hurt", and no answer at all — which falls back to what the
        # type implies.
        if payload.injury_class == "none":
            incident.injury_class = None
        else:
            incident.injury_class = payload.injury_class or t.injury_class
        incident.mol_reportable = t.mol_reportable
        if t.category_code:
            incident.category_id, incident.category_label = await _resolve_category(
                db, t.category_code)
        incident.extra = {**(incident.extra or {}), "incident_type": t.code}
    elif payload.injury_class:
        incident.injury_class = None if payload.injury_class == "none" else payload.injury_class
    # An anonymous report keeps the reporter id for administrative recovery but
    # drops the name, which is what actually appears on screen and in exports.
    if payload.is_anonymous:
        incident.reported_by_name = None

    db.add(incident)
    await db.flush()

    for person in payload.persons:
        db.add(IncidentPerson(
            id=uuid.uuid4(), incident_id=incident.id, **person.model_dump()
        ))
    await db.flush()

    # Start any legal clock the report itself already establishes.
    #
    # These used to begin only at classification, which quietly gave away the
    # part of the window that matters most: OHSA s.51(1) runs forty-eight hours
    # from the *occurrence*, not from the moment HSE got round to opening the
    # record. Somebody reporting "Unconscious" has already told us it is
    # reportable — waiting for a formal classification to notice burns hours of
    # a legal deadline against an injury nobody has yet acted on.
    #
    # `ensure_incident_deadlines` is idempotent and derives which clocks apply
    # from the incident's own fields, so classifying later neither duplicates
    # these nor is prevented from adding one the reporter could not know about.
    if incident.mol_reportable or incident.injury_class:
        await deadline_service.ensure_incident_deadlines(db, incident)

    return incident


async def get(db: AsyncSession, incident_id: uuid.UUID) -> Incident:
    incident = (
        await db.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return incident


async def load_persons(db: AsyncSession, incident_id: uuid.UUID) -> list[IncidentPerson]:
    return list((await db.execute(
        select(IncidentPerson).where(IncidentPerson.incident_id == incident_id)
    )).scalars())


async def load_deadlines(db: AsyncSession, incident_id: uuid.UUID) -> list[StatutoryDeadline]:
    return list((await db.execute(
        select(StatutoryDeadline)
        .where(StatutoryDeadline.source_type == "incident",
               StatutoryDeadline.source_id == incident_id)
        .order_by(StatutoryDeadline.due_at)
    )).scalars())


async def list_(
    db: AsyncSession,
    *,
    statuses: list[str] | None = None,
    form_kind: str | None = None,
    location_id: uuid.UUID | None = None,
    reported_by: uuid.UUID | None = None,
    occurred_from: date | None = None,
    occurred_to: date | None = None,
    injury_classes: list[str] | None = None,
    department_id: uuid.UUID | None = None,
    no_department: bool = False,
    body_part_label: str | None = None,
    injured_only: bool = False,
    recordable_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    order: str = "newest",
) -> list[Incident]:
    stmt = select(Incident)
    if reported_by:
        stmt = stmt.where(Incident.reported_by == reported_by)
    # ★ These four exist so a figure on the dashboard can be opened and show
    # the records it was counted from. A drill-down that lands on a list
    # filtered by something *near* the figure is worse than none: the reader
    # counts what is on screen, gets a different number, and stops trusting
    # both. Each one therefore matches how `services/dashboard.py` groups —
    # including by body-part *label*, which is the snapshot the chart groups
    # by, and including the null department that the chart calls "Not
    # recorded".
    if injury_classes:
        stmt = stmt.where(Incident.injury_class.in_(injury_classes))
    if injured_only:
        stmt = stmt.where(Incident.injury_class.is_not(None))
    if recordable_only:
        # The same OR the recordable tile counts. Injury class alone would miss
        # an incident that is only MOL-reportable, and the rate on the
        # dashboard would then be higher than the list behind it.
        stmt = stmt.where(
            or_(Incident.injury_class.in_(("medical_aid", "lost_time")),
                Incident.mol_reportable.is_(True)))
    if no_department:
        stmt = stmt.where(Incident.department_id.is_(None))
    elif department_id:
        stmt = stmt.where(Incident.department_id == department_id)
    if body_part_label:
        stmt = stmt.where(
            Incident.id.in_(
                select(IncidentPerson.incident_id)
                .where(IncidentPerson.body_part_label == body_part_label)))
    # The date range is applied here rather than in the browser. The list is
    # capped at fifty by default, so a range filtered after the fact would be
    # a range over the fifty newest incidents and would show nothing at all for
    # last spring — while looking exactly like a spring with no incidents.
    start, end = day_bounds(occurred_from, occurred_to)
    if start:
        stmt = stmt.where(Incident.occurred_at >= start)
    if end:
        stmt = stmt.where(Incident.occurred_at < end)
    if statuses:
        stmt = stmt.where(Incident.status.in_(statuses))
    if form_kind:
        stmt = stmt.where(Incident.form_kind == form_kind)
    if location_id:
        stmt = stmt.where(Incident.location_id == location_id)
    if order == "status":
        # By how far along it is — the open ones first, the way somebody
        # working the register reads it — and newest first within each.
        stage = case(
            {"draft": 0, "submitted": 1, "under_investigation": 2,
             "pending_closure": 3, "closed": 4},
            value=Incident.status, else_=5)
        stmt = stmt.order_by(stage, Incident.occurred_at.desc().nullslast())
    elif order == "oldest":
        stmt = stmt.order_by(Incident.occurred_at.asc().nullslast())
    else:
        stmt = stmt.order_by(Incident.occurred_at.desc().nullslast())
    stmt = stmt.limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars())


async def submit(db: AsyncSession, incident: Incident, *, now: datetime | None = None) -> Incident:
    if incident.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Incident {incident.incident_no} has already been submitted",
        )
    incident.status = "submitted"
    incident.submitted_at = now or datetime.now(timezone.utc)
    await db.flush()
    return incident


async def classify(
    db: AsyncSession,
    incident: Incident,
    payload: IncidentClassify,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[Incident, list[StatutoryDeadline]]:
    """Record the two classification facts and start whatever clocks they owe.

    Re-classifying is allowed — the first assessment at the scene is often
    revised once someone has seen a doctor, and an injury that turns out to be
    lost time must start its WSIB clock at that point. Deadlines already
    created are never removed: a clock that started is part of the record.
    """
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Submit the incident before classifying it",
        )

    incident.injury_class = payload.injury_class
    incident.mol_reportable = payload.mol_reportable
    incident.mol_reportable_reason = payload.mol_reportable_reason
    if payload.employer_aware_at is not None:
        incident.employer_aware_at = payload.employer_aware_at
    elif incident.employer_aware_at is None:
        # Nobody supplied it, so the moment of classification is the earliest
        # defensible answer to "when did the employer know".
        incident.employer_aware_at = now or datetime.now(timezone.utc)

    incident.classified_at = now or datetime.now(timezone.utc)
    incident.classified_by = user_id
    incident.classified_by_name = await _resolve_name(db, user_id)
    if incident.status == "submitted":
        incident.status = "under_investigation"
    await db.flush()

    created = await deadline_service.ensure_incident_deadlines(db, incident)
    return incident, created


# HSE: every incident where a worker was injured or equipment was damaged must
# be reviewed by HSE before it can be closed. A near miss with nobody hurt and
# nothing broken does not need the second pair of eyes.
def needs_hse_review(incident: Incident) -> bool:
    if incident.form_kind == "equipment":
        return True
    return bool(incident.injury_class or incident.mol_reportable)


async def hse_review(db: AsyncSession, incident: Incident, reviewer_id: uuid.UUID) -> Incident:
    """Record that HSE has looked at this one."""
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the incident before reviewing it")
    incident.hse_reviewed_at = datetime.now(timezone.utc)
    incident.hse_reviewed_by = reviewer_id
    incident.hse_reviewed_by_name = await _resolve_name(db, reviewer_id)
    await db.flush()
    return incident


def assert_open(incident: Incident, what: str) -> None:
    """Refuse a change to a closed incident.

    A closed incident is a finished record. It is what an inspector is shown,
    what the corrective actions were raised from, and what the statutory clocks
    were measured against — so it stopped being a working document when it was
    closed, and editing it afterwards changes history with nothing to say it
    happened.

    The refusal names reopening rather than just saying no. "Read only for
    ever" is the wrong answer to a genuine correction: people work around it,
    usually by raising a second incident for the same event, which is worse
    than an edit anybody can see. Reopening is deliberate, restricted to the
    role that closed it, and leaves a record.
    """
    if incident.status == "closed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.incident_no} is closed, so {what} cannot be changed. "
            "Reopen it first — HSE can, and the reopening is recorded.",
        )


def withdraw_review(incident: Incident) -> None:
    """Changing the substance of an incident un-reviews it.

    HSE reviewed what the record said at the time. Leaving the review in place
    across a change to the investigation or the classification would let an
    incident reach `closed` carrying a review of something else — which makes
    the gate a formality, and the gate is the whole of the control on closing.
    """
    if incident.hse_reviewed_at is None:
        return
    incident.hse_reviewed_at = None
    incident.hse_reviewed_by = None
    incident.hse_reviewed_by_name = None


async def reopen(db: AsyncSession, incident: Incident, reopener_id: uuid.UUID) -> Incident:
    """Put a closed incident back into investigation, on the record.

    The review goes with it: whatever is about to change, the reviewed record
    is not the one that will be closed the second time.
    """
    if incident.status != "closed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.incident_no} is not closed")
    incident.status = "under_investigation"
    incident.closed_at = None
    incident.reopened_at = datetime.now(timezone.utc)
    incident.reopened_by = reopener_id
    incident.reopened_by_name = await _resolve_name(db, reopener_id)
    withdraw_review(incident)
    await db.flush()
    return incident


async def open_actions_on(db: AsyncSession, incident_id: uuid.UUID) -> list[str]:
    """The numbers of corrective actions raised from this incident and not done."""
    from app.models.action import Action

    return list((await db.execute(
        select(Action.action_no).where(
            Action.source_type == "incident",
            Action.source_id == incident_id,
            Action.status.notin_(("closed", "cancelled")))
        .order_by(Action.action_no))).scalars())


async def close(db: AsyncSession, incident: Incident) -> Incident:
    """Close an incident, refusing where HSE has not reviewed it."""
    if incident.status == "closed":
        return incident
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the incident before closing it")
    if needs_hse_review(incident) and incident.hse_reviewed_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "HSE must review an injury or equipment-damage incident before it is closed",
        )
    # ★ Round 6: "when an incident has a capa that is not closed, do not allow
    # the incident to be closed." A closed incident with work still owed on it
    # reads as finished in every report while the fix it promised is not done.
    open_actions = await open_actions_on(db, incident.id)
    if open_actions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(open_actions)} corrective action{'s are' if len(open_actions) != 1 else ' is'} "
            f"still open on {incident.incident_no}: {', '.join(open_actions)}. "
            "Close or cancel them first.")
    incident.status = "closed"
    incident.closed_at = datetime.now(timezone.utc)
    await db.flush()
    return incident


async def get_person(
    db: AsyncSession, incident_id: uuid.UUID, person_id: uuid.UUID,
) -> IncidentPerson:
    """One person on one incident.

    Scoped by incident as well as by id on purpose: an id from another
    incident would otherwise be edited through this incident's permission
    check, which is the quiet way a record gets changed by somebody who was
    never allowed near it.
    """
    person = (await db.execute(
        select(IncidentPerson).where(
            IncidentPerson.id == person_id,
            IncidentPerson.incident_id == incident_id,
        )
    )).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nobody by that id on this incident")
    return person
