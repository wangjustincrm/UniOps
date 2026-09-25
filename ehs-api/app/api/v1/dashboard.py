"""The management dashboard, in one call.

One endpoint rather than eight: the page draws all of it at once, and eight
round trips on a phone is eight chances to render half a dashboard.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.models.dashboard_layout import DashboardLayout
from app.services import dashboard as svc

router = APIRouter(prefix="/dashboard")


def _user_id(payload: dict) -> uuid.UUID:
    return uuid.UUID(payload["sub"])


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
    # The divisor actually used, so a reader can see the rate is real rather
    # than wondering whether the tile is stale.
    hours_worked: float | None = None
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
    # Present where the bar can be opened onto the records it counted and the
    # label alone would not select them — a department, whose name is not what
    # the register filters by.
    id: str | None = None


class ParticipationRow(BaseModel):
    person: str
    toolbox_talk: int
    good_catch: int
    hazard_assessment: int


class JhscMonth(BaseModel):
    # The meeting, not the month it fell in: two meetings in one September are
    # two rows with the same month, and the screen needs to tell them apart.
    id: str
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


# ── Which panels a person wants, and in what order ──────────────────────────

# The library. Kept on the server so that a panel added to the product appears
# in everybody's chooser without each of them being handed a new frontend.
PANELS: tuple[tuple[str, str], ...] = (
    ("injuries_by_month", "Injuries by month"),
    ("near_misses", "Near misses and good catches"),
    ("participation", "Safety participation"),
    ("injured_departments", "Where people are getting hurt"),
    ("body_parts", "Part of the body affected"),
    ("jhsc_attendance", "JHSC meeting attendance"),
)
DEFAULT_PANELS = [key for key, _ in PANELS]


class PanelOut(BaseModel):
    key: str
    label: str


class LayoutOut(BaseModel):
    panels: list[str]
    available: list[PanelOut]


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    panels: list[str]


@router.get("/layout", response_model=LayoutOut)
async def get_layout(db: SessionDep, user: CanReadIncident):
    """Mine. An empty stored list means the default order, which is also what
    a person who has never touched this gets."""
    row = await db.get(DashboardLayout, _user_id(user))
    chosen = [p for p in (row.panels if row else []) if p in DEFAULT_PANELS]
    return LayoutOut(
        panels=chosen or DEFAULT_PANELS,
        available=[PanelOut(key=k, label=l) for k, l in PANELS],
    )


@router.put("/layout", response_model=LayoutOut)
async def set_layout(payload: LayoutIn, db: SessionDep, user: CanReadIncident):
    """Unknown keys are dropped rather than refused.

    A layout saved before a panel was retired would otherwise become
    unsaveable, and the person would be stuck with an error on a screen they
    were only rearranging.
    """
    wanted = [p for p in payload.panels if p in DEFAULT_PANELS]
    row = await db.get(DashboardLayout, _user_id(user))
    if row is None:
        row = DashboardLayout(user_id=_user_id(user), panels=wanted)
        db.add(row)
    else:
        row.panels = wanted
    await db.flush()
    return LayoutOut(
        panels=wanted or DEFAULT_PANELS,
        available=[PanelOut(key=k, label=l) for k, l in PANELS],
    )
