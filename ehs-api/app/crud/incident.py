"""Incident persistence and the state changes that carry legal weight."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, case, or_, select, text
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
    from app.services import equipment_ref
    await equipment_ref.check(db, payload.equipment_id)
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
        batch_code=(payload.batch_code or "").strip() or None,
        equipment_id=payload.equipment_id,
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
        if not incident_type.is_offered(t.code):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"'{t.label}' has been retired; pick another kind")
        # ★ Three outcomes, not two: a class the reporter chose, an explicit
        # "nobody was hurt", and no answer at all — which falls back to what the
        # type implies.
        if payload.injury_class == "none" or t.form_kind == "near_miss":
            # Round 12, Q4: a near miss hurt nobody, whatever was sent.
            incident.injury_class = None
        else:
            incident.injury_class = payload.injury_class or t.injury_class
        incident.mol_reportable = t.mol_reportable
        if t.category_code:
            incident.category_id, incident.category_label = await _resolve_category(
                db, t.category_code)
        elif t.category_id:
            incident.category_id, incident.category_label = t.category_id, t.category_label
        # ★ The label as it reads now, kept on the record (review L2): HSE can
        # rename their own types, and a rename must not rewrite old reports.
        incident.extra = {**(incident.extra or {}), "incident_type": t.code,
                          "incident_type_label": t.label}
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
    equipment_id: uuid.UUID | None = None,
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
    has_root_causes: bool = False,
    has_actions: bool = False,
    actions_state: str | None = None,
    investigation_outstanding_only: bool = False,
) -> list[Incident]:
    stmt = select(Incident)
    if investigation_outstanding_only:
        stmt = stmt.where(investigation_outstanding())
    if reported_by:
        stmt = stmt.where(Incident.reported_by == reported_by)
    # ★ Round 8, TC-08: the register's figures open the incidents behind them.
    # Matched to how register_summary counts: root causes marked root, and
    # corrective actions raised on the incident and not cancelled.
    if has_root_causes:
        from app.models.incident import Cause
        stmt = stmt.where(select(Cause.id).where(
            Cause.incident_id == Incident.id, Cause.cause_type == "root").exists())
    if has_actions or actions_state:
        from app.models.action import Action
        from app.crud.action import OPEN_STATUSES
        # ★ Round 10, TC-10: completed and outstanding as well as raised.
        wanted = {"completed": [Action.status == "closed"],
                  "outstanding": [Action.status.in_(OPEN_STATUSES)]}.get(
                      actions_state or "", [Action.status != "cancelled"])
        stmt = stmt.where(select(Action.id).where(
            Action.source_id == Incident.id, *wanted).exists())
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
    if equipment_id:
        stmt = stmt.where(Incident.equipment_id == equipment_id)
    if order == "status":
        # By how far along it is — the open ones first, the way somebody
        # working the register reads it — and newest first within each.
        stage = case(
            {"draft": 0, "submitted": 1, "under_investigation": 2,
             "pending_closure": 3, "closed": 4},
            value=Incident.status, else_=5)
        stmt = stmt.order_by(stage, Incident.occurred_at.desc().nullslast(),
                               Incident.id)
    elif order == "oldest":
        stmt = stmt.order_by(Incident.occurred_at.asc().nullslast(), Incident.id)
    else:
        # ★ Review (uniops-8b): the register is read 200 at a time with an
        # OFFSET, and two incidents at the same instant had no fixed order —
        # one could appear on two pages and another on none. The id settles it.
        stmt = stmt.order_by(Incident.occurred_at.desc().nullslast(), Incident.id)
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


def investigation_outstanding():
    """Round 12, Q6 and TC-05: "An investigation is outstanding until it is
    signed off." Counted on incidents still open whose investigation is owed —
    every injury and equipment incident, as `needs_hse_review` has it — or has
    been started, and that no signed investigation closes. One clause, for the
    register's box and the list it opens, so the two cannot disagree.
    """
    from sqlalchemy import exists
    from app.models.incident import IncidentInvestigation as Inv
    started = exists().where(Inv.incident_id == Incident.id)
    signed = exists().where(Inv.incident_id == Incident.id, Inv.completed_at.is_not(None))
    return and_(
        Incident.status.not_in(("draft", "closed")),
        ~signed,
        or_(Incident.form_kind == "equipment", Incident.injury_class.is_not(None),
            Incident.mol_reportable.is_(True), started))


CLOCK_NAMES = {"mol_48h": "the MOL 48-hour report", "wsib_form7": "the WSIB Form 7"}


async def open_clocks(db: AsyncSession, incident_id: uuid.UUID) -> list[str]:
    """The Ministry and WSIB clocks on this incident not yet discharged."""
    from app.models.statutory import StatutoryDeadline
    rows = (await db.execute(select(StatutoryDeadline.kind, StatutoryDeadline.due_at).where(
        StatutoryDeadline.source_type == "incident",
        StatutoryDeadline.source_id == incident_id,
        StatutoryDeadline.kind.in_(tuple(CLOCK_NAMES)),
        StatutoryDeadline.satisfied_at.is_(None)))).all()
    from app.services import statutory
    return [f"{CLOCK_NAMES[k]} (due {due.astimezone(statutory.ONTARIO):%-d %b %Y %H:%M})"
            for k, due in rows]


def _clocks_refusal(incident: Incident, clocks: list[str], doing: str) -> HTTPException:
    # ★ Round 7: "if a mol or wsib clock is started but not ended, the incident
    # should not be allowed to close or the HSE review signed off as completed."
    # Said with the way out: a clock that turned out not to apply (the injury
    # was reclassified) is marked not required, with the reason, on the
    # incident — otherwise this would hold the incident open for ever.
    what = " and ".join(clocks)
    one = len(clocks) == 1
    return HTTPException(
        status.HTTP_409_CONFLICT,
        f"{what[:1].upper()}{what[1:]} on {incident.incident_no} "
        f"{'is' if one else 'are'} still running, so it cannot be {doing}. "
        f"Record {'it' if one else 'each'} as filed under Statutory on the incident, or "
        f"mark {'it' if one else 'any that does not apply'} not required there with the reason.")


async def hse_review(db: AsyncSession, incident: Incident, reviewer_id: uuid.UUID) -> Incident:
    """Record that HSE has looked at this one."""
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Submit the incident before reviewing it")
    clocks = await open_clocks(db, incident.id)
    if clocks:
        raise _clocks_refusal(incident, clocks, "signed off as reviewed")
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


# How reclassify closes a clock the new kind does not owe — and so how it
# knows, if the kind changes back, which closed clocks are its own to reopen.
NO_LONGER_APPLIES = "Not required: no longer applies — incident type changed"


async def reclassify(db: AsyncSession, incident: Incident, code: str,
                     injury_class: str | None, *, user_id: uuid.UUID) -> Incident:
    """Change what kind of incident this was, before it closes.

    ★ Round 7 (answer box): "a medical incident may become a lost time injury
    after the initial report is created. allow for the investigator to
    reclassify the injury by selecting a different What kind of incident pick
    list before the incident is closed."

    Within the form it came in on: a medical report becomes another kind of
    medical incident; becoming an equipment report would be a different form,
    and is refused. What the kind implies is derived again — the injury class,
    the Ministry flag, the category — and HSE's review is withdrawn, since what
    was reviewed has changed.

    The clocks follow (raised in review by uniops-55): a clock the new kind
    owes is started; one still running that it no longer owes is closed as
    "No longer applies — incident type changed from X to Y", so it neither
    lingers on the calendar nor holds the incident open for ever. A clock
    already met is left as it is. Every field change is in the change log.
    """
    from app.services import deadlines as deadline_service
    from app.services import statutory
    from app.models.statutory import StatutoryDeadline

    assert_open(incident, "the kind of incident")
    if incident.status == "draft":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A draft is the reporter's; it can be changed once it is sent")
    new = incident_type.BY_CODE.get(code)
    if new is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown kind of incident '{code}'")
    if new.form_kind != incident.form_kind:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{new.label.split(' — ')[0]} is reported on a different form. Raise it as a new "
            "report and refer to this one in it.")
    old_code = (incident.extra or {}).get("incident_type")
    old = incident_type.BY_CODE.get(old_code) if isinstance(old_code, str) else None
    wanted = None if injury_class == "none" else injury_class
    if old_code == code and (injury_class is None or wanted == incident.injury_class):
        return incident
    if old_code != code and not incident_type.is_offered(code):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"'{new.label}' has been retired; pick another kind")

    extra = {**(incident.extra or {}), "incident_type": new.code}
    if old_code != code:
        extra["incident_type_label"] = new.label
    incident.extra = extra
    # ★ Review (uniops-8b): the dialog sends only the kind, so this used to
    # take the new kind's default every time — an occupational illness with
    # lost time became no class at all, WSIB's Form 7 stopped being owed and
    # was closed as "no longer applies". What was chosen is kept unless the
    # new kind fixes the class, or the class was only the old kind's own.
    if injury_class is not None:
        chosen = injury_class
    elif new.injury_class:
        chosen = new.injury_class
    elif incident.injury_class and (old is None or incident.injury_class not in (
            old.injury_class, old.default_injury_class)):
        chosen = incident.injury_class
    else:
        chosen = new.default_injury_class
    # "none" is how the picker says no injury; it is stored as no class, as
    # `create` does — stored literally it counted as an injury.
    incident.injury_class = None if chosen == "none" or new.form_kind == "near_miss" else chosen
    # A Ministry flag HSE set by classifying, with its reason, stays theirs.
    incident.mol_reportable = new.mol_reportable or bool(incident.mol_reportable_reason)
    if new.category_code:
        incident.category_id, incident.category_label = await _resolve_category(
            db, new.category_code)
    elif new.category_id:
        incident.category_id, incident.category_label = new.category_id, new.category_label
    elif old is not None and old.category_id and incident.category_id == old.category_id:
        incident.category_id, incident.category_label = None, None
    elif old is not None and old.category_code and incident.category_id is not None:
        # ★ Round 12, REP-01: the category the old type put there went with
        # it — a food-safety near miss changed to a plain one stayed in Food
        # Safety on every report. A category somebody chose by hand stays.
        old_id, _ = await _resolve_category(db, old.category_code)
        if incident.category_id == old_id:
            incident.category_id, incident.category_label = None, None
    withdraw_review(incident)
    await db.flush()

    created = await deadline_service.ensure_incident_deadlines(db, incident)
    # ★ Review (uniops-8b): WSIB's three business days run from when the
    # employer learned of what makes it reportable. A first aid injury found
    # two weeks later to have cost time started its new clock from the
    # occurrence (or the first classification) — overdue the moment it
    # existed. As `classify` says, it starts when the class is changed to one
    # that owes it. The Ministry's clock runs from the occurrence regardless.
    now = datetime.now(timezone.utc)
    for d in created:
        if d.kind == "wsib_form7" and d.starts_at < now:
            d.starts_at = now
            d.due_at = statutory.compute_due(d.kind, now, await deadline_service.load_holidays(db))
    owed = {kind for kind, _ in deadline_service.clocks_for_incident(incident)}
    was = old.label.split(" — ")[0] if old else "unrecorded"
    now_label = new.label.split(" — ")[0]
    name = await _resolve_name(db, user_id)
    for d in (await db.execute(select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "incident",
            StatutoryDeadline.source_id == incident.id))).scalars().all():
        if d.satisfied_at is None and d.kind not in owed:
            await deadline_service.satisfy(
                db, d, user_id=user_id, user_name=name,
                evidence_note=f"{NO_LONGER_APPLIES} from {was} to {now_label}",
                when=datetime.now(timezone.utc))
        elif (d.satisfied_at is not None and d.kind in owed
              and (d.evidence_note or "").startswith(NO_LONGER_APPLIES)):
            # ★ Review (uniops-8b): changed back. A clock this function closed
            # is owed again, and ensure_incident_deadlines never recreates a
            # kind it has seen — so Unconscious → First Aid → Unconscious left
            # the Ministry's 48 hours closed and the incident free to close.
            # Reopened as it was, from when it started; the sweep raises its
            # task again. One HSE closed by hand as not required stays closed.
            d.satisfied_at = None
            d.satisfied_by = None
            d.satisfied_by_name = None
            d.evidence_note = None
            d.escalation_level = 0
    await db.flush()
    return incident


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


async def record_event(
    db: AsyncSession, incident: Incident, kind: str, *, by_id: uuid.UUID | None,
    detail: str | None = None,
) -> None:
    """One row of the incident's timeline: who, when, what (round 9)."""
    from app.models.incident import IncidentEvent
    db.add(IncidentEvent(
        id=uuid.uuid4(), incident_id=incident.id, kind=kind,
        at=datetime.now(timezone.utc), by_id=by_id,
        by_name=await _resolve_name(db, by_id) if by_id else None, detail=detail))
    await db.flush()


async def load_events(db: AsyncSession, incident_id: uuid.UUID):
    from app.models.incident import IncidentEvent
    return list((await db.execute(
        select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.at))).scalars())


async def reopen(
    db: AsyncSession, incident: Incident, reopener_id: uuid.UUID, *, reason: str,
) -> Incident:
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
    await record_event(db, incident, "reopened", by_id=reopener_id, detail=reason.strip())
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


async def close(db: AsyncSession, incident: Incident,
                user_id: uuid.UUID | None = None) -> Incident:
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
    clocks = await open_clocks(db, incident.id)
    if clocks:
        raise _clocks_refusal(incident, clocks, "closed")
    # ★ Round 7: "when a return to work plan is created in an incident, the
    # incident can not be closed until the return to work form indicates they
    # are back to full duties."
    from app.models.rtw import RtwPlan
    if (await db.execute(select(RtwPlan.id).where(
            RtwPlan.incident_id == incident.id, RtwPlan.status == "active").limit(1))).first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The return-to-work plan on {incident.incident_no} is still active. When the "
            "worker is back on full duties, press \"Back on full duties\" on the plan, then "
            "close the incident.")
    incident.status = "closed"
    incident.closed_at = datetime.now(timezone.utc)
    # Round 12, Q5: closing it is HSE's review of it.
    from app.services import capa_review, tasks as task_service
    # ★ Review L3: by whoever closed it — this is a person's review.
    await capa_review.close_for(db, task_service.DOC_INCIDENT, incident.id, user_id)
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
