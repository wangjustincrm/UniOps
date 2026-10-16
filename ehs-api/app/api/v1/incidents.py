"""Incident endpoints.

Reporting is open to every authenticated employee — `ehs.incident.report` is
granted to every role — because the whole point of the module is that the
person who saw it can file it. Everything after reporting is gated more
narrowly.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.core.permissions import (
    CanClassifyIncident,
    CanCloseIncident,
    CanInvestigate,
    CanManageStatutory,
    CanReadIncident,
    CanReportIncident,
)
from app.models.incident import Incident, IncidentPerson
from sqlalchemy import select
from app.models.mirrors import User
from uniops_authz import has_permission

from pydantic import BaseModel, ConfigDict, Field

from app.services import incident_type
from app.crud import incident as crud
from app.services import notify
from app.crud import investigation as inv_crud
from app.schemas.incident import (
    IncidentClassify,
    IncidentEdit,
    IncidentCreate,
    IncidentListItem,
    IncidentOut,
    IncidentPersonOut,
    MolReportFiledIn,
)
from app.schemas.wsib import Form7FiledIn, Form7Package
from app.schemas.investigation import (
    CauseActionOut,
    CauseOut,
    CausesReplace,
    CauseTreeOut,
    InvestigationIn,
    InvestigationOut,
    InvestigationSignIn,
)

router = APIRouter()


async def _may_read(db, payload: dict, incident) -> bool:
    """Whether this caller may see this incident.

    ehs.incident.read means "browse the register", and most people do not have
    it. But the person who filed a report, and anyone recorded as involved in
    one, can always see their own — without that, a worker submitting a report
    is bounced off the page they were just sent to, which is what happened.
    """
    if await has_permission(db, _user_id(payload), payload.get("role", ""),
                            "ehs.incident.read"):
        return True
    me = _user_id(payload)
    if incident.reported_by == me:
        return True
    involved = (await db.execute(
        select(IncidentPerson.id).where(
            IncidentPerson.incident_id == incident.id,
            IncidentPerson.user_id == me,
        ).limit(1)
    )).scalar_one_or_none()
    return involved is not None


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


async def sees_anonymous_reporters(db, user: dict) -> bool:
    """Only HSE learns who filed an anonymous report.

    Round 6, box 4: "an anonymous report ... will stay that way when other
    supervisors view it. only the HSE role can see what user ID was used to
    submit the incident to allow for further private discussions." Held to
    `ehs.incident.close`, which only the HSE Manager has.
    """
    return await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.incident.close")


async def hidden_reporters(db, user: dict, incident_ids) -> dict[uuid.UUID, uuid.UUID]:
    """For records hung on incidents: incident id → the reporter to keep hidden.

    Only anonymous reports, and only for a caller who is not HSE. A risk
    assessment, an action or a photo the reporter made on their own anonymous
    report would otherwise sign it with their name.
    """
    ids = {i for i in incident_ids if i is not None}
    if not ids or await sees_anonymous_reporters(db, user):
        return {}
    rows = (await db.execute(
        select(Incident.id, Incident.reported_by)
        .where(Incident.id.in_(ids), Incident.is_anonymous.is_(True),
               Incident.reported_by.is_not(None)))).all()
    return {r.id: r.reported_by for r in rows}


WITHHELD = "Withheld — anonymous report"


async def withheld_reporter(db, incident, user: dict) -> tuple[uuid.UUID, str | None] | None:
    """The anonymous reporter to keep hidden from this caller: their id and name.

    None for a named report, and for HSE, who alone may know who it was.
    """
    if not incident.is_anonymous or incident.reported_by is None:
        return None
    if await sees_anonymous_reporters(db, user):
        return None
    reporter = await db.get(User, incident.reported_by)
    return incident.reported_by, (reporter.full_name if reporter else None)


def is_the_reporter(person, hidden: tuple[uuid.UUID, str | None]) -> bool:
    """Whether a person named on the report is the anonymous reporter — by
    account, or, for a name typed in, by that name."""
    rid, name = hidden
    if person.user_id is not None:
        return person.user_id == rid
    return bool(name) and (person.person_name or "").strip().casefold() == name.casefold()


async def _to_out(db, incident, user: dict) -> IncidentOut:
    out = IncidentOut.model_validate(incident)
    out.persons = [  # type: ignore[assignment]
        p for p in await crud.load_persons(db, incident.id)
    ]
    out.deadlines = [  # type: ignore[assignment]
        d for d in await crud.load_deadlines(db, incident.id)
    ]
    out = IncidentOut.model_validate(out.model_dump())
    if incident.is_anonymous:
        if await sees_anonymous_reporters(db, user):
            # HSE: who it was, by name, for the private conversation.
            reporter = await db.get(User, incident.reported_by) if incident.reported_by else None
            out.anonymous_reporter_name = reporter.full_name if reporter else None
        else:
            # ★ Everybody else: nothing that leads back to a person. The name
            # was already dropped; the user id, the signature and the signer's
            # name were still being sent to anyone who could open it.
            out.reported_by = None
            out.signature = None
            out.signer_name = None
            out.signer_role = None
            # ★ Round 7, TC-08: "supervisor can view who was injured on an
            # anonymous medical incident." Somebody reporting their own injury
            # is named as the injured person, which names the reporter. Their
            # row keeps the injury and loses the name.
            hidden = await withheld_reporter(db, incident, user)
            if hidden is not None:
                for person in out.persons:
                    if is_the_reporter(person, hidden):
                        person.person_name = WITHHELD
                        person.user_id = None
    return out


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def create_incident(payload: IncidentCreate, db: SessionDep, user: CanReportIncident):
    incident = await crud.create(db, payload, reporter_id=_user_id(user))
    # ★ Not told yet: this is a draft. The notice goes on submit — see below.
    return await _to_out(db, incident, user)


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    db: SessionDep,
    user: CanReportIncident,
    status_in: list[str] | None = Query(default=None, alias="status"),
    form_kind: str | None = None,
    location_id: uuid.UUID | None = None,
    occurred_from: date | None = None,
    occurred_to: date | None = None,
    injury_class: list[str] | None = Query(default=None),
    department_id: uuid.UUID | None = None,
    no_department: bool = Query(
        default=False,
        description="Incidents with no department — what the dashboard calls "
                    "'Not recorded'."),
    body_part_label: str | None = None,
    injured_only: bool = Query(
        default=False, description="Only those where somebody was hurt."),
    recordable_only: bool = Query(
        default=False,
        description="What the recordable rate counts: medical aid, lost time, "
                    "or reportable to the Ministry."),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    # Round 6, box 1: "allow the incident register list to be sorted by status."
    # On the server, so it orders the whole register rather than the fifty rows
    # that happened to arrive.
    order: str = Query(default="newest", pattern="^(newest|oldest|status)$"),
):
    # Without ehs.incident.read the register is not browsable, but a person's
    # own reports are: the list narrows rather than refusing outright.
    mine_only = not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.incident.read")
    rows = await crud.list_(
        db, statuses=status_in, form_kind=form_kind,
        location_id=location_id, limit=limit, offset=offset,
        occurred_from=occurred_from, occurred_to=occurred_to,
        injury_classes=injury_class, department_id=department_id,
        no_department=no_department, body_part_label=body_part_label,
        injured_only=injured_only, recordable_only=recordable_only,
        reported_by=_user_id(user) if mine_only else None, order=order,
    )
    return [IncidentListItem.model_validate(r) for r in rows]


class RegisterSummary(BaseModel):
    open: int
    under_investigation: int
    closed: int
    root_causes: int
    corrective_actions: int


@router.get("/summary", response_model=RegisterSummary)
async def register_summary(db: SessionDep, user: CanReportIncident):
    """The figures above the register.

    Round 7, TC-10: "create a dashboard on the incident registry with the
    following boxes, Open Incidents, number of closed incidents, Incidents under
    investigation, number of Root causes identified, number of CAPA's recorded."

    Over what this person's register shows: every incident with the register
    permission, their own reports without it — the same narrowing as the list,
    so a figure never counts rows the list would not show. ★ Drafts are not
    incidents yet and are left out, as everywhere else they are counted.
    Corrective actions cancelled are not counted as recorded.
    """
    from app.models.action import Action
    from app.models.incident import Cause
    from sqlalchemy import func

    mine_only = not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.incident.read")
    seen = select(Incident.id).where(Incident.status != "draft")
    if mine_only:
        seen = seen.where(Incident.reported_by == _user_id(user))

    by_status = dict((await db.execute(
        select(Incident.status, func.count()).where(Incident.id.in_(seen))
        .group_by(Incident.status))).all())
    roots = (await db.execute(
        select(func.count()).select_from(Cause)
        .where(Cause.incident_id.in_(seen), Cause.cause_type == "root"))).scalar() or 0
    actions = (await db.execute(
        select(func.count()).select_from(Action)
        .where(Action.source_id.in_(seen), Action.status != "cancelled"))).scalar() or 0
    return RegisterSummary(
        open=sum(n for st, n in by_status.items() if st != "closed"),
        under_investigation=by_status.get("under_investigation", 0),
        closed=by_status.get("closed", 0),
        root_causes=roots, corrective_actions=actions)


class IncidentTypeOption(BaseModel):
    code: str
    label: str
    form_kind: str
    asks_injury_class: bool
    # Whether this type is one where the WSIB detail is worth asking for on the
    # report itself. Served rather than decided in the frontend for the same
    # reason the list is: a second copy of the rule would drift from this one.
    injury_detail: bool
    # What the injury picker opens on for this type. None means no default.
    default_injury_class: str | None = None


@router.get("/types", response_model=list[IncidentTypeOption])
async def list_incident_types(form_kind: str | None = None):
    """The incident types HSE's form offers, optionally for one form.

    Served rather than hardcoded in the frontend: the list changed once already
    (three options became twelve on 31 August) and a second copy would drift.
    """
    types = incident_type.for_form(form_kind) if form_kind else list(incident_type.TYPES)
    return [IncidentTypeOption(code=t.code, label=t.label, form_kind=t.form_kind,
                               asks_injury_class=t.asks_injury_class,
                               default_injury_class=t.default_injury_class,
                               injury_detail=t.injury_detail) for t in types]


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    incident = await crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open incidents you reported or were involved in",
        )
    return await _to_out(db, incident, user)


@router.post("/{incident_id}/submit", response_model=IncidentOut)
async def submit_incident(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """File a draft report.

    ★ Checked nothing at all: anybody able to report an incident could submit
    anybody else's draft, which files a statutory record in somebody else's
    name — and a draft is the one state where the person writing it has not
    finished deciding what it says.
    """
    incident = await crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only submit reports you filed or were involved in",
        )
    await crud.submit(db, incident)
    # ★ Told when it is sent, not when it is created. It used to go out on
    # create, so a report whose create landed and whose submit did not left HSE
    # with a notice about a draft, and the submit that followed sent nothing.
    # crud.submit refuses anything but a draft, so this runs once per incident.
    # Same transaction: a submit that fails to save leaves no notice behind.
    await notify.incident_reported(db, incident)
    return await _to_out(db, incident, user)


async def _clocks_counted_from(db, incident) -> bool:
    """Whether this incident has a deadline that runs from when it happened —
    owed or already met (a filed report states the time)."""
    from app.models.statutory import StatutoryDeadline
    from app.services.deadlines import counted_from_occurrence
    return (await db.execute(
        select(StatutoryDeadline.id).where(
            StatutoryDeadline.source_type == "incident",
            StatutoryDeadline.source_id == incident.id,
            StatutoryDeadline.kind.in_(counted_from_occurrence(incident)))
        .limit(1))).first() is not None


@router.patch("/{incident_id}", response_model=IncidentOut)
async def edit_incident(
    incident_id: uuid.UUID, payload: IncidentEdit, db: SessionDep, user: CanClassifyIncident,
):
    """Correct the account of what happened. Round 6, box 4: "Supervisors shall
    have edit rights to all incidents, near misses and hazard assessments."

    Held to the investigate key, which area supervisors and HSE hold. A closed
    incident is reopened first, as for every other change to it; and a change to
    the account withdraws HSE's review, because what was reviewed is no longer
    what the record says. Every field is in the change log under the editor's
    name — an anonymous report stays anonymous: editing it records the editor,
    not the reporter.
    """
    incident = await crud.get(db, incident_id)
    if incident.status == "draft":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A draft is the reporter's; it can be edited once it is sent")
    crud.assert_open(incident, "the report")
    fields = payload.model_dump(exclude_unset=True)
    if "title" in fields and not fields["title"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A report needs a title")
    if "occurred_at" in fields and fields["occurred_at"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A report needs to say when it happened")
    # ★ Round-6 review: a legal clock counted from when it happened (the MOL
    # 48 hours; WSIB where awareness was not recorded) must not be left counted
    # from a time the record no longer says. HSE may correct the time, and the
    # clocks still owed are recounted from it; anybody else is refused.
    previous = incident.occurred_at
    recount = False
    if ("occurred_at" in fields and fields["occurred_at"] != previous
            and await _clocks_counted_from(db, incident)):
        if not await has_permission(db, _user_id(user), user.get("role", ""),
                                    "ehs.incident.close"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Only HSE can change when this happened, because statutory deadlines "
                "are counted from it.")
        recount = True
    if "location_id" in fields:
        incident.location_path = await crud._resolve_location_path(db, fields["location_id"])
    for key, value in fields.items():
        setattr(incident, key, value)
    if fields:
        crud.withdraw_review(incident)
    await db.flush()
    if recount:
        from app.services import deadlines as deadline_service
        await deadline_service.recount_from_occurrence(db, incident)
    return await _to_out(db, incident, user)


@router.post("/{incident_id}/classify", response_model=IncidentOut)
async def classify_incident(
    incident_id: uuid.UUID,
    payload: IncidentClassify,
    db: SessionDep,
    user: CanClassifyIncident,
):
    """Set the injury class and the Ministry-reportable flag, and start the
    statutory clocks those two facts owe.

    The response carries the deadlines that now exist, so the caller can show
    the countdown immediately rather than re-fetching.
    """
    incident = await crud.get(db, incident_id)
    crud.assert_open(incident, "how it is classified")
    crud.withdraw_review(incident)
    was = incident.status
    await crud.classify(db, incident, payload, user_id=_user_id(user),
                        now=datetime.now(timezone.utc))
    # Only when this is what moved it. Re-classifying later is a correction to
    # an investigation already under way, not the start of one.
    if was != incident.status:
        await notify.incident_status_changed(
            db, incident, incident.status, by_name=incident.classified_by_name)
    return await _to_out(db, incident, user)


# ── Investigation and causes ────────────────────────────────────────────────


@router.post("/{incident_id}/hse-review", response_model=IncidentOut)
async def hse_review_incident(
    incident_id: uuid.UUID, db: SessionDep, user: CanCloseIncident,
):
    """Record HSE's review — the gate an injury or damage incident passes to close.

    Held behind `ehs.incident.close`, which only the HSE Manager has. A
    supervisor may investigate; the review is deliberately somebody else.
    """
    incident = await crud.get(db, incident_id)
    await crud.hse_review(db, incident, _user_id(user))
    await notify.incident_status_changed(
        db, incident, "hse_reviewed", by_name=incident.hse_reviewed_by_name)
    return await _to_out(db, incident, user)


@router.post("/{incident_id}/close", response_model=IncidentOut)
async def close_incident(incident_id: uuid.UUID, db: SessionDep, user: CanCloseIncident):
    """Close an incident. Refused where HSE has not reviewed one that needs it."""
    incident = await crud.get(db, incident_id)
    was = incident.status
    await crud.close(db, incident)
    # Closing an already-closed incident is a no-op, and says nothing twice.
    if was != incident.status:
        await notify.incident_status_changed(
            db, incident, "closed",
            by_name=await crud._resolve_name(db, _user_id(user)))
    return await _to_out(db, incident, user)


@router.get("/{incident_id}/investigation", response_model=InvestigationOut | None)
async def get_investigation(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    incident = await crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your incident")
    inv = await inv_crud.get_investigation(db, incident_id)
    return InvestigationOut.model_validate(inv) if inv else None


@router.post("/{incident_id}/reopen", response_model=IncidentOut)
async def reopen_incident(
    incident_id: uuid.UUID, db: SessionDep, user: CanCloseIncident,
):
    """Put a closed incident back into investigation.

    Behind the same permission as closing: whoever may finish a record may
    unfinish it, and nobody else. The HSE review goes with it — whatever is
    about to change, the reviewed version is not the one that will be closed
    the second time.
    """
    incident = await crud.get(db, incident_id)
    await crud.reopen(db, incident, _user_id(user))
    await notify.incident_status_changed(
        db, incident, "reopened", by_name=incident.reopened_by_name)
    return await _to_out(db, incident, user)


@router.put("/{incident_id}/investigation", response_model=InvestigationOut)
async def upsert_investigation(
    incident_id: uuid.UUID, payload: InvestigationIn, db: SessionDep, user: CanClassifyIncident,  # noqa: ARG001
):
    incident = await crud.get(db, incident_id)
    crud.assert_open(incident, "the investigation")
    crud.withdraw_review(incident)
    inv = await inv_crud.upsert_investigation(db, incident, payload)
    return InvestigationOut.model_validate(inv)


@router.post("/{incident_id}/investigation/sign", response_model=InvestigationOut)
async def sign_investigation(
    incident_id: uuid.UUID, payload: InvestigationSignIn, db: SessionDep, user: CanClassifyIncident,
):
    incident = await crud.get(db, incident_id)
    crud.assert_open(incident, "the investigation")
    inv = await inv_crud.sign_investigation(
        db, incident, payload.signature, user_id=_user_id(user),
        now=datetime.now(timezone.utc))
    return InvestigationOut.model_validate(inv)


@router.put("/{incident_id}/causes", response_model=CauseTreeOut)
async def replace_causes(
    incident_id: uuid.UUID, payload: CausesReplace, db: SessionDep, user: CanClassifyIncident,  # noqa: ARG001
):
    incident = await crud.get(db, incident_id)
    crud.assert_open(incident, "the causes")
    crud.withdraw_review(incident)
    await inv_crud.replace_causes(db, incident, payload)
    return await _cause_tree(db, incident)


@router.get("/{incident_id}/cause-tree", response_model=CauseTreeOut)
async def cause_tree(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """Causes with the corrective actions hanging off each one.

    A root cause with nothing under it is flagged rather than left to the
    reader to notice — that gap is exactly what an audit looks for.
    """
    incident = await crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open incidents you reported or were involved in",
        )
    return await _cause_tree(db, incident)


async def _cause_tree(db, incident) -> CauseTreeOut:
    causes = await inv_crud.list_causes(db, incident.id)
    grouped = await inv_crud.actions_by_cause(db, incident.id)

    def _to_out(cause) -> CauseOut:
        actions = grouped.get(cause.id, [])
        out = CauseOut.model_validate(cause)
        out.actions = [CauseActionOut.model_validate(a) for a in actions]
        # Only root causes are expected to have actions; an immediate cause is
        # a description of what happened, not something to fix.
        out.needs_action = cause.cause_type == "root" and not actions
        return out

    immediate = [_to_out(c) for c in causes if c.cause_type == "immediate"]
    root = [_to_out(c) for c in causes if c.cause_type == "root"]
    return CauseTreeOut(
        incident_id=incident.id,
        incident_no=incident.incident_no,
        immediate=immediate,
        root=root,
        unaddressed_root_causes=sum(1 for c in root if c.needs_action),
    )


# ── WSIB Form 7 ─────────────────────────────────────────────────────────────


class PersonInjuryPatch(BaseModel):
    """Correcting what was recorded about an injury.

    The reporter names the body part from memory, often within minutes and
    sometimes from the other end of a radio. The person who knows is whoever
    assesses the injury afterwards, which is the investigation step — so the
    fields captured on the report have to be correctable there, or the figures
    keep the first guess forever.

    Ids and labels move together: the label is the snapshot that keeps the
    record readable after the vocabulary is renamed, the id is what the
    injury figures group on.
    """

    model_config = ConfigDict(extra="forbid")

    body_part_id: uuid.UUID | None = None
    body_part_label: str | None = Field(default=None, max_length=200)
    nature_of_injury_id: uuid.UUID | None = None
    nature_of_injury_label: str | None = Field(default=None, max_length=200)
    treatment: str | None = None
    sent_offsite: bool | None = None
    follow_up: str | None = None


@router.patch("/{incident_id}/persons/{person_id}", response_model=IncidentPersonOut)
async def update_person_injury(
    incident_id: uuid.UUID, person_id: uuid.UUID,
    payload: PersonInjuryPatch, db: SessionDep, user: CanInvestigate,  # noqa: ARG001
):
    incident = await crud.get(db, incident_id)
    # Same gate as the investigation itself: a closed incident is a closed
    # record, and correcting one means reopening it on purpose.
    crud.assert_open(incident, "the injury detail")
    person = await crud.get_person(db, incident_id, person_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(person, field, value)
    await db.flush()
    return person


@router.post("/{incident_id}/mol-report/filed", response_model=IncidentOut)
async def mol_report_filed(
    incident_id: uuid.UUID, payload: MolReportFiledIn, db: SessionDep, user: CanManageStatutory,
):
    """Record that the s.51(1) written report went to the Ministry, stopping that clock.

    Every other clock in this table had a way to be discharged and this one did
    not, so it ran forever: WSIB's was stopped by recording the form, JHSC's by
    writing the reply, while the Ministry's 48 hours kept counting after the
    report had been sent — which is how the first acceptance round found it,
    with one incident showing an overdue legal obligation that had in fact been
    met days earlier. An obligation nobody can discharge is worse than no
    countdown at all: it trains people to ignore the one screen that must not
    be ignored.

    Certificate and policy-review clocks are not in the same position. Those
    roll — renewing or reviewing moves the date out — so they are never
    "satisfied" and need nothing here.
    """
    from app.services import deadlines as deadline_service

    incident = await crud.get(db, incident_id)
    deadline = next(
        (d for d in await crud.load_deadlines(db, incident.id) if d.kind == "mol_48h"),
        None,
    )
    if deadline is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.incident_no} owes the Ministry nothing — it is not marked "
            "as reportable under OHSA s.51(1)",
        )
    if deadline.satisfied_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The Ministry report for {incident.incident_no} was already recorded "
            f"on {deadline.satisfied_at.date().isoformat()}",
        )

    await deadline_service.satisfy(
        db, deadline,
        user_id=_user_id(user),
        user_name=None,
        evidence_file_id=payload.evidence_file_id,
        evidence_note=payload.reference,
        when=payload.filed_at or datetime.now(timezone.utc),
    )
    return await _to_out(db, incident, user)


@router.get("/{incident_id}/wsib-form7", response_model=Form7Package)
async def wsib_form7(incident_id: uuid.UUID, db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Everything needed to fill in Form 7 in one sitting.

    UniOps does not file it — statutory submission goes through WSIB's own
    service. What this returns is the fields we hold plus the ones we do not,
    so whoever files knows what to gather before they start.
    """
    from app.services.wsib import build_package
    incident = await crud.get(db, incident_id)
    package = await build_package(db, incident)
    # ★ Round 7, TC-08: the worker on Form 7 is the injured person — on a
    # self-reported anonymous injury, the reporter. HSE files it and sees them.
    hidden = await withheld_reporter(db, incident, user)
    if hidden is not None and any(
            p.role == "injured" and is_the_reporter(p, hidden)
            for p in await crud.load_persons(db, incident.id)):
        package.worker_name = WITHHELD
        package.worker_employee_no = None
        package.worker_department = None
        package.worker_hire_date = None
    return package


@router.post("/{incident_id}/wsib-form7/filed", response_model=Form7Package)
async def wsib_form7_filed(
    incident_id: uuid.UUID, payload: Form7FiledIn, db: SessionDep, user: CanManageStatutory,
):
    """Record that Form 7 was filed, which is what stops the clock.

    The confirmation number WSIB returns is the evidence the obligation was
    discharged, so it is required rather than optional — a deadline marked
    satisfied with nothing behind it is worse than one still showing as open.
    """
    from app.services import deadlines as deadline_service
    from app.services.wsib import build_package

    incident = await crud.get(db, incident_id)
    deadline = next(
        (d for d in await crud.load_deadlines(db, incident.id) if d.kind == "wsib_form7"),
        None,
    )
    if deadline is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{incident.incident_no} owes WSIB nothing — it has not been classified "
            "as a medical-aid or lost-time injury",
        )
    if deadline.satisfied_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Form 7 for {incident.incident_no} was already recorded as filed on "
            f"{deadline.satisfied_at.date().isoformat()}",
        )

    await deadline_service.satisfy(
        db, deadline,
        user_id=_user_id(user),
        user_name=None,
        evidence_file_id=payload.evidence_file_id,
        evidence_note=payload.confirmation_number,
        when=payload.filed_at or datetime.now(timezone.utc),
    )
    return await build_package(db, incident)
