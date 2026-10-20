"""Risk assessments against HSE's matrix.

The rating is computed here, not accepted from the client: the grid is the
company's document and an assessment that disagrees with it is worse than no
assessment. What the caller chooses is the likelihood, the severity, and which
consequence type the severity was judged on.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.v1.incidents import _may_read, _user_id, hidden_reporters
from uniops_authz import has_permission
from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.crud.incident import _resolve_name
from app.models.incident import Incident
from app.models.risk import RiskAssessment, RiskLikelihood, RiskSeverity

router = APIRouter(prefix="/risk")

CONSEQUENCE_TYPES = ("people", "downtime", "reputation", "environmental", "asset_damage")


class LikelihoodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    qualitative: str | None
    frequency: str | None
    ratings: dict


class SeverityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    level: int
    people: str | None
    downtime: str | None
    reputation: str | None
    environmental: str | None
    asset_damage: str | None


class ConsequenceOut(BaseModel):
    code: str
    label: str
    # Level → the matrix's wording for this consequence. A level with none
    # cannot be scored, and a consequence with none is not offered.
    severity: dict[str, str]


class MatrixOut(BaseModel):
    likelihood: list[LikelihoodOut]
    severity: list[SeverityOut]
    consequence_types: list[str]
    # Round 8: "Judged on" is HSE's list under Settings → Lists.
    consequences: list[ConsequenceOut] = Field(default_factory=list)


class AssessmentIn(BaseModel):
    source_type: str | None = Field(default=None, max_length=20)
    source_id: uuid.UUID | None = None
    hazard_id: uuid.UUID | None = None
    hazard_label: str | None = Field(default=None, max_length=200)
    description: str | None = None
    consequence_type: str = Field(default="people")
    likelihood_code: str = Field(max_length=4)
    severity_level: int = Field(ge=0, le=5)
    controls: str | None = None
    residual_likelihood_code: str | None = Field(default=None, max_length=4)
    residual_severity_level: int | None = Field(default=None, ge=0, le=5)
    # Round 8: the hazard from the hazard list, and the controls picked.
    hazard_item_id: uuid.UUID | None = None
    hierarchy_control_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    hazard_control_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str | None
    source_id: uuid.UUID | None
    hazard_label: str | None
    description: str | None
    consequence_type: str
    likelihood_code: str
    likelihood_label: str | None
    severity_level: int
    severity_label: str | None
    rating: str
    controls: str | None
    residual_likelihood_code: str | None
    residual_severity_level: int | None
    residual_rating: str | None
    hazard_item_id: uuid.UUID | None = None
    hierarchy_controls: list = Field(default_factory=list)
    hazard_controls: list = Field(default_factory=list)
    assessed_by_name: str | None
    assessed_at: datetime
    # The name is withheld: the anonymous reporter made this assessment.
    by_anonymous_reporter: bool = False


_BUILT_IN_LABELS = {
    "people": "People", "downtime": "Unplanned downtime", "reputation": "Reputation",
    "environmental": "Environmental", "asset_damage": "Asset damage",
}


async def _consequences(db) -> list[ConsequenceOut]:
    """What an assessment can be judged on, with the wording for each level.

    ★ Round 8: HSE's list under Settings → Lists ("Judged on"), Equipment
    added. The wording lives on each entry (attrs.severity). Where the list has
    not been set up — a database built before migration 0069 ran — the five
    the matrix has columns for are used, with its wording.
    """
    from app.models.vocabulary import VocabularyItem
    items = (await db.execute(select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == "consequence_type",
        VocabularyItem.is_active.is_(True)).order_by(VocabularyItem.sort_order))).scalars().all()
    if items:
        return [ConsequenceOut(
            code=i.code, label=i.label,
            severity={str(k): str(v) for k, v in ((i.attrs or {}).get("severity") or {}).items() if v})
            for i in items]
    bands = (await db.execute(select(RiskSeverity).order_by(RiskSeverity.level))).scalars().all()
    return [ConsequenceOut(code=c, label=_BUILT_IN_LABELS.get(c, c),
                           severity={str(b.level): getattr(b, c) for b in bands if getattr(b, c, None)})
            for c in CONSEQUENCE_TYPES]


async def _snapshot(db, vocabulary: str, ids: list[uuid.UUID]) -> list[dict]:
    """Entries from a list, as {id, label}, refusing any not on it."""
    from app.models.vocabulary import VocabularyItem
    if not ids:
        return []
    rows = {r.id: r for r in (await db.execute(select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == vocabulary, VocabularyItem.id.in_(ids)))).scalars()}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Not on the {vocabulary.replace('_', ' ')} list: {missing[0]}")
    return [{"id": str(i), "label": rows[i].label} for i in dict.fromkeys(ids)]


@router.get("/matrix", response_model=MatrixOut)
async def get_matrix(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """The whole grid, so a client can render it and show what a choice means."""
    likelihood = list((await db.execute(
        select(RiskLikelihood).order_by(RiskLikelihood.sort_order))).scalars().all())
    severity = list((await db.execute(
        select(RiskSeverity).order_by(RiskSeverity.level))).scalars().all())
    consequences = await _consequences(db)
    return MatrixOut(likelihood=likelihood, severity=severity,
                     consequence_types=[c.code for c in consequences],
                     consequences=consequences)


async def _rate(db: SessionDep, likelihood_code: str, severity_level: int) -> tuple[str, str]:
    row = await db.get(RiskLikelihood, likelihood_code)
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unknown likelihood '{likelihood_code}'")
    rating = (row.ratings or {}).get(str(severity_level))
    if rating is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The matrix has no rating for likelihood {likelihood_code} at severity "
            f"{severity_level}",
        )
    return rating, row.label


@router.post("/assessments", response_model=AssessmentOut, status_code=201)
async def create_assessment(
    payload: AssessmentIn, db: SessionDep, user: CanReportIncident,
):
    if not await _may_see(db, user, payload.source_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only assess incidents you reported or were involved in")
    consequences = {c.code: c for c in await _consequences(db)}
    judged = consequences.get(payload.consequence_type)
    if judged is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown consequence type. One of: {', '.join(consequences)}")

    rating, likelihood_label = await _rate(db, payload.likelihood_code, payload.severity_level)

    # Snapshot the wording for the consequence type actually used, not just the
    # number — the matrix gets revised.
    severity_label = judged.severity.get(str(payload.severity_level))

    # Round 8: the hazard from the hazard list names it; the controls are kept
    # as they were called when picked.
    hazard_label = payload.hazard_label
    if payload.hazard_item_id is not None:
        hazard_label = (await _snapshot(db, "hazard", [payload.hazard_item_id]))[0]["label"]
    hierarchy_controls = await _snapshot(db, "hierarchy_of_control", payload.hierarchy_control_ids)
    hazard_controls = await _snapshot(db, "hazard_control", payload.hazard_control_ids)

    residual_rating = None
    if payload.residual_likelihood_code and payload.residual_severity_level is not None:
        residual_rating, _ = await _rate(
            db, payload.residual_likelihood_code, payload.residual_severity_level)

    row = RiskAssessment(
        id=uuid.uuid4(),
        source_type=payload.source_type, source_id=payload.source_id,
        hazard_id=payload.hazard_id, hazard_label=hazard_label,
        hazard_item_id=payload.hazard_item_id,
        hierarchy_controls=hierarchy_controls, hazard_controls=hazard_controls,
        description=payload.description,
        consequence_type=payload.consequence_type,
        likelihood_code=payload.likelihood_code, severity_level=payload.severity_level,
        rating=rating, likelihood_label=likelihood_label, severity_label=severity_label,
        controls=payload.controls,
        residual_likelihood_code=payload.residual_likelihood_code,
        residual_severity_level=payload.residual_severity_level,
        residual_rating=residual_rating,
        assessed_by=_user_id(user),
        assessed_by_name=await _resolve_name(db, _user_id(user)),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def _may_see(db, user: dict, source_id: uuid.UUID | None) -> bool:
    """Whether this caller may read the assessments of that record.

    ★ An assessment belongs to the record it is about, so it follows that
    record's own rule rather than having one of its own. Reading an incident
    needs ehs.incident.read, or being the person who reported it or was
    involved — and an assessment of an incident states what could happen, how
    bad, and who judged it. Served on a looser permission it is the incident,
    read around the incident's gate. The third acceptance round found exactly
    this: the incident answered 403 and its assessments answered 200.

    A hazard or a job analysis is deliberately open to everybody. Both are
    plant-wide safety information a worker is meant to consult before starting,
    and their own registers are readable on the same permission.
    """
    if source_id is None:
        return True  # the caller named no record; the register check handles that

    # ★ Ask the source table, not the caller and not the assessments.
    #
    # Two ways round this have already been found in review. Trusting
    # `source_type` from the query string left `?source_id=<somebody else's
    # incident>` with no type at all as a way in, and `?source_type=hazard` with
    # the same id as another. Reading the type off the assessments already
    # recorded fixed the reading and reopened the writing: creating the *first*
    # assessment on somebody else's incident found no rows to read a type from,
    # so it went through — and a risk assessment of another department's injury,
    # signed by whoever wrote it, had been filed.
    #
    # Only an incident has a gate, so only an incident has to be identified: if
    # this id names one, it is judged as one, whatever it was called. A hazard or
    # a job analysis is plant-wide safety information and stays open.
    incident = await db.get(Incident, source_id)
    if incident is None:
        return True
    return await _may_read(db, user, incident)


@router.get("/assessments", response_model=list[AssessmentOut])
async def list_assessments(
    db: SessionDep, user: CanReportIncident,
    source_type: str | None = None, source_id: uuid.UUID | None = None,
    limit: int = Query(default=100, le=300),
):
    # ★ Asking without naming a record is browsing every assessment on site,
    # incidents included. That is the register's permission, not the reporting
    # one — and a caller who omits the filter must not get what a caller who
    # names an incident would be refused.
    if source_id is None:
        if not await has_permission(db, _user_id(user), user.get("role", ""),
                                    "ehs.incident.read"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Say which record's assessments you want. Listing them across "
                "records needs permission to browse the incident register.")
    elif not await _may_see(db, user, source_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only see the assessments of incidents you reported or "
            "were involved in")

    q = select(RiskAssessment)
    if source_type:
        q = q.where(RiskAssessment.source_type == source_type)
    if source_id:
        q = q.where(RiskAssessment.source_id == source_id)
    rows = list((await db.execute(
        q.order_by(RiskAssessment.assessed_at.desc()).limit(limit))).scalars().all())
    # ★ Round-6 review: a worker may make the first assessment of the report
    # they filed, and on an anonymous report that signed it with their name.
    hidden = await hidden_reporters(db, user, [r.source_id for r in rows])
    out = [AssessmentOut.model_validate(r) for r in rows]
    for o, r in zip(out, rows):
        if r.source_id in hidden and r.assessed_by == hidden[r.source_id]:
            o.assessed_by_name = None
            o.by_anonymous_reporter = True
    return out
