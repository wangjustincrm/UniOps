"""The hazard register and job safety analyses.

Permissions reuse the corrective-action keys rather than adding two more pairs.
A hazard is the same kind of object to the same people — something identified,
owned, and worked on — and every role that may raise or manage a CAPA is
exactly the set that may raise or manage a hazard. Two more keys would be two
more things to get wrong in the matrix, and they would drift.

Signing a JSA is the exception: every worker signs one before starting a job,
so it is gated on the key everybody holds.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from uniops_authz import has_permission

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadAction, CanReportIncident, CanWriteAction
from app.crud import cause as cause_crud
from app.schemas.action import ActionOut
from app.schemas.investigation import CausesReplace
from app.crud import hazard as crud

router = APIRouter()

_CONTROL_LEVELS = "^(elimination|substitution|engineering|administrative|ppe)$"


# ── Hazards ──────────────────────────────────────────────────────────────────

class HazardIn(BaseModel):
    # A key this model does not know is a mistake, not something to drop
    # quietly: a misspelled field name would otherwise read as "the user
    # cleared it" and overwrite a good value with nothing.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=250)
    description: str | None = None
    # What was found when somebody went and looked, as distinct from what the
    # hazard is.
    findings: str | None = None
    hazard_type_id: uuid.UUID | None = None
    hazard_type_label: str | None = Field(default=None, max_length=200)
    # ★ Round 12, HAZ-11: several kinds and several controls from HSE's lists.
    # When sent, the first kind becomes `hazard_type_id` too.
    hazard_type_ids: list[uuid.UUID] | None = Field(default=None, max_length=30)
    hazard_control_ids: list[uuid.UUID] | None = Field(default=None, max_length=60)
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    existing_controls: str | None = None
    # Where the control sits on the hierarchy. "We gave them gloves" reads
    # differently beside "we removed the pinch point", and an auditor asks.
    control_level: str | None = Field(default=None, pattern=_CONTROL_LEVELS)
    owner_id: uuid.UUID | None = None
    # ★ UAT 14: staff who helped with the assessment, besides the owner.
    helper_ids: list[uuid.UUID] | None = Field(default=None, max_length=30)
    identified_on: date | None = None
    review_due_on: date | None = None
    source_type: str | None = Field(default=None, max_length=20)
    source_id: uuid.UUID | None = None


class HazardPatch(HazardIn):
    title: str | None = Field(default=None, max_length=250)
    # open | controlled | eliminated. `controlled` is not closed: a guarded
    # pinch point is still a pinch point and comes back round for review.
    status: str | None = Field(default=None, pattern="^(open|controlled|eliminated)$")


class HazardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hazard_no: str
    title: str
    description: str | None
    findings: str | None = None
    created_by: uuid.UUID | None = None
    created_by_name: str | None = None
    # Both halves: the label is the snapshot that keeps an old record readable
    # after the vocabulary is renamed, the id is what puts the picker back on
    # the right entry when the record is reopened for correction. Returning
    # only the label meant editing a hazard silently cleared its kind.
    hazard_type_id: uuid.UUID | None
    hazard_type_label: str | None
    hazard_types: list = Field(default_factory=list)
    hazard_controls: list = Field(default_factory=list)
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    equipment_id: uuid.UUID | None
    risk_assessment_id: uuid.UUID | None
    current_rating: str | None
    residual_rating: str | None
    existing_controls: str | None
    control_level: str | None
    owner_id: uuid.UUID | None
    owner_name: str | None
    helpers: list = Field(default_factory=list)
    identified_on: date | None
    review_due_on: date | None
    status: str
    created_at: datetime
    # Kirk, round 12: "last revised on" — an edit, not an assessment.
    revised_at: datetime | None = None
    revised_by_name: str | None = None


class RatingCount(BaseModel):
    rating: str
    count: int


async def _writes_hazards(db, user: dict) -> bool:
    return await has_permission(db, _user_id(user), user.get("role", ""), "ehs.action.write")


async def _readable(db, user: dict, hazard_id: uuid.UUID):
    """★ Round 12, Q11: someone who cannot write hazards sees one once it has
    been assessed ("complete once it has at least one assessment"). Before
    that it is somebody's draft, and 404 rather than 403 says nothing of it."""
    hazard = await crud.get_hazard(db, hazard_id)
    if hazard.risk_assessment_id is None and not await _writes_hazards(db, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hazard not found")
    return hazard


@router.get("/hazards", response_model=list[HazardOut])
async def list_hazards(
    db: SessionDep, user: CanReadAction,
    status_in: list[str] | None = Query(default=None, alias="status"),
    location_id: uuid.UUID | None = None,
    rating: str | None = None,
    order: str = Query(default="newest", pattern="^(newest|risk)$"),
):
    """★ Round 12, HAZ-07: newest first by default, worst first on request.
    Round 3 had it ranked by risk only."""
    return await crud.list_hazards(
        db, status_in=status_in, location_id=location_id, rating=rating, order=order,
        assessed_only=not await _writes_hazards(db, user))


class HazardCauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cause_type: str
    vocabulary_item_id: uuid.UUID | None
    label: str
    note: str | None
    sort_order: int


class HazardCauseTree(BaseModel):
    """The register entry's causes, split the way the screen shows them, with
    the actions raised against each one.

    Same shape as the investigation's tree, because it is the same question asked
    of a different record — and a root cause with nothing under it has to read as
    visibly incomplete in both places.
    """

    hazard_no: str
    immediate: list[HazardCauseOut]
    root: list[HazardCauseOut]
    actions: dict[uuid.UUID, list[ActionOut]]


@router.get("/hazards/{hazard_id}/causes", response_model=HazardCauseTree)
async def hazard_causes(hazard_id: uuid.UUID, db: SessionDep, user: CanReadAction):
    hazard = await _readable(db, user, hazard_id)
    rows = await cause_crud.list_for(db, hazard_id=hazard.id)
    grouped = await cause_crud.actions_by_cause(
        db, source_type="hazard", source_id=hazard.id)
    return HazardCauseTree(
        hazard_no=hazard.hazard_no,
        immediate=[HazardCauseOut.model_validate(c) for c in rows
                   if c.cause_type == "immediate"],
        root=[HazardCauseOut.model_validate(c) for c in rows if c.cause_type == "root"],
        actions={cid: [ActionOut.model_validate(a) for a in acts]
                 for cid, acts in grouped.items()},
    )


@router.put("/hazards/{hazard_id}/causes", response_model=HazardCauseTree)
async def replace_hazard_causes(
    hazard_id: uuid.UUID, payload: CausesReplace, db: SessionDep, user: CanWriteAction,
):
    """Record what caused it, and raise the work in the same request.

    ★ Round 5, answer box 11. The action travels with the cause for the reason it
    does on an investigation: an action points at a cause and a cause has no id
    until it is saved, so doing it in two steps from the client means guessing
    which id belongs to which of the rows just typed.
    """
    hazard = await crud.get_hazard(db, hazard_id)
    await cause_crud.replace(
        db,
        causes=payload.causes,
        source_type="hazard",
        source_id=hazard.id,
        source_ref=hazard.hazard_no,
        hazard_id=hazard.id,
    )
    return await hazard_causes(hazard_id, db, user)


class HazardPage(BaseModel):
    items: list[HazardOut]
    total: int


@router.get("/hazards/register", response_model=HazardPage)
async def hazard_register(
    db: SessionDep, user: CanReadAction,
    search: str | None = Query(default=None, max_length=200),
    status_in: list[str] | None = Query(default=None, alias="status"),
    recorded_from: date | None = None, recorded_to: date | None = None,
    sort: str | None = Query(default=None, description="A key of the register's columns; unknown -> 422"),
    dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """The register, a page at a time, with the total. Newest first unless a
    column is chosen (HAZ-07). Someone who cannot write hazards sees the
    assessed ones (Q11), as everywhere else."""
    rows, total = await crud.hazard_register(
        db, search=search, status_in=status_in, recorded_from=recorded_from,
        recorded_to=recorded_to, sort=sort, dir=dir, offset=offset, limit=limit,
        assessed_only=not await _writes_hazards(db, user))
    return HazardPage(items=[HazardOut.model_validate(r) for r in rows], total=total)


@router.get("/hazards/summary", response_model=list[RatingCount])
async def hazard_summary(db: SessionDep, user: CanReadAction):  # noqa: ARG001
    return await crud.hazard_summary(db)


@router.post("/hazards", response_model=HazardOut, status_code=status.HTTP_201_CREATED)
async def create_hazard(payload: HazardIn, db: SessionDep, user: CanWriteAction):
    return await crud.create_hazard(db, payload.model_dump(), creator_id=_user_id(user))


@router.get("/hazards/{hazard_id}", response_model=HazardOut)
async def get_hazard(hazard_id: uuid.UUID, db: SessionDep, user: CanReadAction):
    return await _readable(db, user, hazard_id)


@router.patch("/hazards/{hazard_id}", response_model=HazardOut)
async def update_hazard(
    hazard_id: uuid.UUID, payload: HazardPatch, db: SessionDep, user: CanWriteAction,
):
    hazard = await crud.get_hazard(db, hazard_id)
    return await crud.update_hazard(db, hazard, payload.model_dump(exclude_unset=True),
                                    editor_id=_user_id(user))


class AssessIn(BaseModel):
    """Assessing a hazard against HSE's matrix.

    The rating is not accepted from the client — it is computed from the grid,
    which is the company's own document. What is chosen is the likelihood, the
    severity, and which consequence the severity was judged on.

    ★ Round 12, HAZ-02: the incident's form. `controls` is what remains once
    the controls are in, and the controls are picked from the two lists. One
    score for the hazard however many kinds it is (Q11).
    """

    consequence_type: str = Field(default="people")
    likelihood_code: str = Field(max_length=4)
    severity_level: int = Field(ge=0, le=5)
    controls: str | None = None
    residual_likelihood_code: str | None = Field(default=None, max_length=4)
    residual_severity_level: int | None = Field(default=None, ge=0, le=5)
    hierarchy_control_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    hazard_control_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)
    # ★ HAZ-12: the server always took status=eliminated and no screen sent it,
    # so the dashboard's Eliminated was 0 for ever. Asked at assessment.
    eliminated: bool = False


@router.post("/hazards/{hazard_id}/assess", response_model=HazardOut)
async def assess_hazard(
    hazard_id: uuid.UUID, payload: AssessIn, db: SessionDep, user: CanWriteAction,
):
    """Score it through the shared risk service and make that the current one."""
    from app.api.v1.risk import AssessmentIn, create_assessment

    hazard = await crud.get_hazard(db, hazard_id)
    fields = payload.model_dump(exclude={"eliminated"})
    assessment = await create_assessment(
        AssessmentIn(
            source_type="hazard", source_id=hazard.id,
            hazard_id=hazard.hazard_type_id, hazard_label=hazard.title,
            description=hazard.description,
            **fields,
        ),
        db, user,
    )
    return await crud.attach_assessment(db, hazard, assessment, eliminated=payload.eliminated)


# ── Job safety analysis ──────────────────────────────────────────────────────

class StepIn(BaseModel):
    step: str = Field(min_length=1)
    hazards: str | None = None
    controls: str | None = None
    control_level: str | None = Field(default=None, pattern=_CONTROL_LEVELS)
    hazard_id: uuid.UUID | None = None
    residual_rating: str | None = Field(default=None, max_length=20)
    # ★ Round 12, JSA-05 / Q12: picked from HSE's lists, one set per step.
    hazard_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    control_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)


class JsaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=250)
    job_description: str | None = None
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    required_ppe: str | None = None
    review_due_on: date | None = None
    # ★ Round 12, JSA-01 and JSA-03.
    system_id: uuid.UUID | None = None
    ppe_ids: list[uuid.UUID] | None = Field(default=None, max_length=40)


class JsaIn(JsaPatch):
    title: str = Field(min_length=1, max_length=250)
    # ★ Round 12, JSA-04: the steps on the same page, saved with it, in one
    # transaction. Approval still checks them.
    steps: list[StepIn] = Field(default_factory=list, max_length=100)


class StepsIn(BaseModel):
    steps: list[StepIn] = Field(default_factory=list, max_length=100)


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    step: str
    hazards: str | None
    controls: str | None
    control_level: str | None
    hazard_id: uuid.UUID | None
    residual_rating: str | None
    hazard_items: list = Field(default_factory=list)
    control_items: list = Field(default_factory=list)


class JsaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    jsa_no: str
    title: str
    job_description: str | None
    version: int
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    equipment_id: uuid.UUID | None
    required_ppe: str | None
    ppe_items: list = Field(default_factory=list)
    system_id: uuid.UUID | None = None
    system_label: str | None = None
    status: str
    approved_at: datetime | None
    approved_by_name: str | None
    review_due_on: date | None
    supersedes_id: uuid.UUID | None
    created_by_name: str | None
    created_at: datetime


class SignerIn(BaseModel):
    user_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=255)
    person_role: str | None = Field(default=None, max_length=150)
    signature: str | None = None
    is_supervisor: bool = False


class SignIn(BaseModel):
    signers: list[SignerIn] = Field(min_length=1)
    # The day the job is done, not the day the record is typed up.
    worked_on: date


class SignoffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    user_id: uuid.UUID | None
    person_name: str
    person_role: str | None
    worked_on: date
    signed_at: datetime
    is_supervisor: bool


async def _jsa_out(db, rows: list) -> list[JsaOut]:
    """With each System's current name (JSA-01, as for a machine)."""
    from app.models.vocabulary import VocabularyItem
    from sqlalchemy import select
    ids = {r.system_id for r in rows if r.system_id}
    labels = dict((await db.execute(select(VocabularyItem.id, VocabularyItem.label)
                                    .where(VocabularyItem.id.in_(ids)))).all()) if ids else {}
    return [JsaOut.model_validate(r).model_copy(update={"system_label": labels.get(r.system_id)})
            for r in rows]


@router.get("/jsa", response_model=list[JsaOut])
async def list_jsa(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    return await _jsa_out(db, await crud.list_jsa(db, status_in=status_in))


@router.post("/jsa", response_model=JsaOut, status_code=status.HTTP_201_CREATED)
async def create_jsa(payload: JsaIn, db: SessionDep, user: CanWriteAction):
    fields = payload.model_dump(exclude={"steps"})
    jsa = await crud.create_jsa(db, fields, creator_id=_user_id(user))
    if payload.steps:
        await crud.replace_steps(db, jsa, [s.model_dump() for s in payload.steps])
    return (await _jsa_out(db, [jsa]))[0]


@router.get("/jsa/{jsa_id}", response_model=JsaOut)
async def get_jsa(jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return (await _jsa_out(db, [await crud.get_jsa(db, jsa_id)]))[0]


@router.patch("/jsa/{jsa_id}", response_model=JsaOut)
async def update_jsa(
    jsa_id: uuid.UUID, payload: JsaPatch, db: SessionDep, user: CanWriteAction,  # noqa: ARG001
):
    """Correct a draft. ★ Nothing on the page called this, so a System or PPE
    picked wrongly, or missing from an analysis made before round 12, could not
    be put right (B3/B4 review M5). Only a draft, as its steps: an approved
    analysis changes by a new version, not under the crew who signed it."""
    jsa = await crud.get_jsa(db, jsa_id)
    if jsa.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{jsa.jsa_no} is {jsa.status}. Create a new version to change it.")
    fields = payload.model_dump(exclude_unset=True)
    if "title" in fields and not (fields["title"] or "").strip():
        # ★ L8: an explicit null reached a NOT NULL column as a 500.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The job needs a name")
    if "location_id" in fields:
        (jsa.location_path, jsa.department_id,
         jsa.department_name) = await crud._place(db, fields["location_id"])
    if "equipment_id" in fields:
        from app.services import equipment_ref
        await equipment_ref.check(db, fields["equipment_id"], jsa.equipment_id)
    await crud.apply_jsa_lists(db, fields, current_system=jsa.system_id,
                               current_ppe=jsa.ppe_items)
    for key, value in fields.items():
        setattr(jsa, key, value)
    await db.flush()
    return (await _jsa_out(db, [jsa]))[0]


@router.get("/jsa/{jsa_id}/steps", response_model=list[StepOut])
async def get_steps(jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    await crud.get_jsa(db, jsa_id)
    return await crud.steps_for(db, jsa_id)


@router.put("/jsa/{jsa_id}/steps", response_model=list[StepOut])
async def put_steps(
    jsa_id: uuid.UUID, payload: StepsIn, db: SessionDep, user: CanWriteAction,  # noqa: ARG001
):
    """Replace the step list. Only while it is a draft."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.replace_steps(db, jsa, [s.model_dump() for s in payload.steps])


@router.post("/jsa/{jsa_id}/activate", response_model=JsaOut)
async def activate_jsa(jsa_id: uuid.UUID, db: SessionDep, user: CanWriteAction):
    """Approve it for use. Refused if a step names a hazard with no control."""
    jsa = await crud.get_jsa(db, jsa_id)
    return (await _jsa_out(db, [await crud.activate(db, jsa, user_id=_user_id(user))]))[0]


@router.post("/jsa/{jsa_id}/new-version", response_model=JsaOut,
             status_code=status.HTTP_201_CREATED)
async def new_jsa_version(jsa_id: uuid.UUID, db: SessionDep, user: CanWriteAction):
    """A draft copy, steps included, that retires this one when approved."""
    jsa = await crud.get_jsa(db, jsa_id)
    return (await _jsa_out(db, [await crud.new_jsa_version(db, jsa, creator_id=_user_id(user))]))[0]


@router.get("/jsa/{jsa_id}/signoffs", response_model=list[SignoffOut])
async def get_signoffs(
    jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    worked_on: date | None = None,
):
    await crud.get_jsa(db, jsa_id)
    return await crud.signoffs_for(db, jsa_id, worked_on=worked_on)


@router.post("/jsa/{jsa_id}/sign", response_model=list[SignoffOut],
             status_code=status.HTTP_201_CREATED)
async def sign_jsa(
    jsa_id: uuid.UUID, payload: SignIn, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
):
    """The crew signs before starting — several at once, from one device."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.sign(
        db, jsa, [s.model_dump() for s in payload.signers], worked_on=payload.worked_on)
