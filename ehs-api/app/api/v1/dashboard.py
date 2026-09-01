"""The management dashboard, in one call.

One endpoint rather than eight: the page draws all of it at once, and eight
round trips on a phone is eight chances to render half a dashboard.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.services import dashboard as svc

router = APIRouter(prefix="/dashboard")


class Headline(BaseModel):
    year: int
    injuries: int
    first_aid: int
    medical: int
    lost_time: int
    recordable: int
    rir: float | None
    days_since_last_incident: int | None
    cost_total: float
    cost_average: float | None
    cost_known_count: int
    lost_time_hours: float
    rir_note: str | None


class MonthRow(BaseModel):
    month: str
    first_aid: int = 0
    medical_aid: int = 0
    lost_time: int = 0


class NearMissRow(BaseModel):
    month: str
    this_year: int
    last_year: int


class CountRow(BaseModel):
    label: str
    count: int


class ParticipationRow(BaseModel):
    person: str
    toolbox_talk: int
    good_catch: int
    hazard_assessment: int


class JhscMonth(BaseModel):
    month: str
    held_on: str
    present: int
    # The attendance sheet's own length, not today's roster: a meeting held
    # before somebody joined is not scored against a committee they were
    # not on yet.
    invited: int


class Jhsc(BaseModel):
    available: bool
    reason: str | None = None
    months: list[JhscMonth] = []
    members: int = 0


class DashboardOut(BaseModel):
    headline: Headline
    medical_events: list[MonthRow]
    near_misses: list[NearMissRow]
    body_parts: list[CountRow]
    injured_departments: list[CountRow]
    participation: list[ParticipationRow]
    jhsc: Jhsc


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    year: int | None = Query(default=None, ge=2000, le=2100),
    hours_worked: float | None = Query(default=None, ge=0),
):
    year = year or datetime.now(timezone.utc).astimezone(svc.ONTARIO).year
    return DashboardOut(
        headline=await svc.headline(db, year=year, hours_worked=hours_worked),
        medical_events=await svc.medical_events_by_month(db, year=year),
        near_misses=await svc.near_misses_by_month(db, year=year),
        body_parts=await svc.body_parts(db, year=year),
        injured_departments=await svc.injured_by_department(db, year=year),
        participation=await svc.participation(db, year=year),
        jhsc=await svc.jhsc_attendance(db, year=year),
    )
