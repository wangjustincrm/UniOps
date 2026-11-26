"""The Food Safety Risk Assessment and its register (UAT 13).

Kirk: "build a separate risk register on all food safety near misses. make this
risk assessment only visible on food safety near misses … title it 'Food Safety
Risk Assessment'." Read by whoever may read the incident; made by whoever may
investigate one (the same people who score an incident's risk).
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from uniops_authz import has_permission

from app.api.v1.incidents import _may_read, _user_id, hidden_reporters
from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident, CanReportIncident
from app.crud import incident as incident_crud
from app.crud import investigation as inv_crud
from app.crud._list_order import sort_clauses
from app.crud.incident import _resolve_name
from app.models.food_safety import FoodSafetyAssessment
from app.models.incident import Incident
from app.services import food_safety as fs

router = APIRouter()


async def is_food_safety_near_miss(db, incident: Incident) -> bool:
    """A near miss in food safety: the Near Miss — Food Safety type, or any
    near miss in the Food Safety category."""
    return incident.form_kind == "near_miss" and await inv_crud.is_food_safety(db, incident)


class Scale(BaseModel):
    value: int
    text: str


class Band(BaseModel):
    code: str
    floor: int
    label: str
    action: str


class ScalesOut(BaseModel):
    probability: list[Scale]
    severity: list[Scale]
    detectability: list[Scale]
    bands: list[Band]
    ccp_rpn: int


@router.get("/food-safety/scales", response_model=ScalesOut)
async def scales(user: CanReportIncident):  # noqa: ARG001
    """HSE's wording for the three ratings and the four bands, served so the
    form and the arithmetic cannot disagree."""
    def rows(d):
        return [Scale(value=k, text=v) for k, v in sorted(d.items())]
    return ScalesOut(probability=rows(fs.PROBABILITY), severity=rows(fs.SEVERITY),
                     detectability=rows(fs.DETECTABILITY), ccp_rpn=fs.CCP_RPN,
                     bands=[Band(code=c, floor=f, label=lbl, action=a) for c, f, lbl, a in fs.BANDS])


class AssessmentIn(BaseModel):
    ccp_failure: bool = False
    probability: int = Field(ge=1, le=5)
    severity: int = Field(ge=1, le=5)
    detectability: int = Field(ge=1, le=5)
    notes: str | None = Field(default=None, max_length=4000)


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    ccp_failure: bool
    probability: int
    severity: int
    detectability: int
    rpn: int
    band: str
    notes: str | None
    assessed_by_name: str | None
    assessed_at: datetime


async def _incident_for(db, user, incident_id) -> Incident:
    incident = await incident_crud.get(db, incident_id)
    if not await _may_read(db, user, incident):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot open this incident")
    if not await is_food_safety_near_miss(db, incident):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The Food Safety Risk Assessment is for food safety near misses only")
    return incident


@router.get("/incidents/{incident_id}/food-safety-assessments", response_model=list[AssessmentOut])
async def list_for_incident(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    await _incident_for(db, user, incident_id)
    rows = list((await db.execute(select(FoodSafetyAssessment).where(
        FoodSafetyAssessment.incident_id == incident_id).order_by(
        FoodSafetyAssessment.assessed_at.desc()))).scalars())
    # ★ Review R7: on an anonymous report, an assessment its reporter made is
    # not signed with their name to anybody but HSE — as risk.py does.
    hidden = await hidden_reporters(db, user, [incident_id])
    out = [AssessmentOut.model_validate(r) for r in rows]
    for o, r in zip(out, rows):
        if r.incident_id in hidden and r.assessed_by == hidden[r.incident_id]:
            o.assessed_by_name = None
    return out


@router.post("/incidents/{incident_id}/food-safety-assessments", response_model=AssessmentOut,
             status_code=status.HTTP_201_CREATED)
async def assess(incident_id: uuid.UUID, payload: AssessmentIn, db: SessionDep,
                 user: CanReportIncident):
    incident = await _incident_for(db, user, incident_id)
    me = _user_id(user)
    if not await has_permission(db, me, user.get("role", ""), "ehs.incident.investigate"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Coordinators, supervisors and HSE assess food safety risk")
    value = fs.rpn(payload.probability, payload.severity, payload.detectability, payload.ccp_failure)
    row = FoodSafetyAssessment(
        id=uuid.uuid4(), incident_id=incident.id, ccp_failure=payload.ccp_failure,
        probability=payload.probability, severity=payload.severity,
        detectability=payload.detectability, rpn=value, band=fs.band(value),
        notes=(payload.notes or "").strip() or None,
        assessed_by=me, assessed_by_name=await _resolve_name(db, me),
        assessed_at=datetime.now(timezone.utc))
    db.add(row)
    await db.flush()
    return row


# ── The register ─────────────────────────────────────────────────────────────

class RegisterRow(BaseModel):
    incident_id: uuid.UUID
    incident_no: str
    title: str
    occurred_at: datetime | None
    location_path: str | None
    status: str
    ccp_failure: bool
    probability: int
    severity: int
    detectability: int
    rpn: int
    band: str
    assessed_by_name: str | None
    assessed_at: datetime


class RegisterPage(BaseModel):
    items: list[RegisterRow]
    total: int


@router.get("/food-safety/register", response_model=RegisterPage)
async def register(
    db: SessionDep, user: CanReadIncident,
    q: str | None = Query(default=None, max_length=100),
    band: str | None = Query(default=None, pattern="^(critical|high|medium|low)$"),
    sort: str | None = None,
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    occurred_from: date | None = None,
    occurred_to: date | None = None,
):
    """Each assessed food-safety near miss, by its latest assessment: "a separate
    risk register on all food safety near misses". Searched, sorted and paged on
    the server."""
    latest = (select(FoodSafetyAssessment,
                     func.row_number().over(partition_by=FoodSafetyAssessment.incident_id,
                                            order_by=FoodSafetyAssessment.assessed_at.desc())
                     .label("n")).subquery())
    a = latest.c
    # ★ Review R14: drafts are not near misses yet, and the dashboard's year
    # comes with its figures, so a band opens exactly what it counted.
    where = [a.n == 1, Incident.status != "draft"]
    from app.services.day_range import day_bounds
    start, end = day_bounds(occurred_from, occurred_to)
    if start is not None:
        where.append(Incident.occurred_at >= start)
    if end is not None:
        where.append(Incident.occurred_at < end)
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"
        where.append(Incident.incident_no.ilike(like, escape="\\")
                     | Incident.title.ilike(like, escape="\\"))
    if band:
        where.append(a.band == band)
    base = select(Incident, a).join(latest, a.incident_id == Incident.id).where(*where)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    order = sort_clauses(sort, dir, {
        "incident_no": Incident.incident_no, "title": func.lower(Incident.title),
        "occurred_at": Incident.occurred_at, "rpn": a.rpn, "assessed_at": a.assessed_at,
    }, [Incident.id]) or [a.rpn.desc(), a.assessed_at.desc(), Incident.id]
    rows = (await db.execute(base.order_by(*order).offset((page - 1) * page_size)
                             .limit(page_size))).all()
    hidden = await hidden_reporters(db, user, [r.Incident.id for r in rows])
    return RegisterPage(total=total, items=[RegisterRow(
        incident_id=r.Incident.id, incident_no=r.Incident.incident_no, title=r.Incident.title,
        occurred_at=r.Incident.occurred_at, location_path=r.Incident.location_path,
        status=r.Incident.status, ccp_failure=r.ccp_failure, probability=r.probability,
        severity=r.severity, detectability=r.detectability, rpn=r.rpn, band=r.band,
        assessed_by_name=(None if hidden.get(r.Incident.id) == r.assessed_by else r.assessed_by_name),
        assessed_at=r.assessed_at) for r in rows])
