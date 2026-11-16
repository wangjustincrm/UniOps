"""The management dashboard, in one call.

One endpoint rather than eight: the page draws all of it at once, and eight
round trips on a phone is eight chances to render half a dashboard.
"""
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReadIncident
from app.models.config import EhsConfig
from app.models.dashboard_layout import DashboardLayout
from app.services import body_map
from app.services import dashboard as svc
from app.services import dashboard_settings as ds
from app.services.day_range import plant_today

router = APIRouter(prefix="/dashboard")


def _user_id(payload: dict) -> uuid.UUID:
    return uuid.UUID(payload["sub"])


class Headline(BaseModel):
    year: int
    incidents: int
    injuries: int
    first_aid: int
    medical: int
    lost_time: int
    recordable: int
    rir: float | None
    lost_time_rate: float | None
    dart_rate: float | None
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


class ResolutionMonth(BaseModel):
    month: str
    reported: int
    closed: int
    average_days: float | None
    still_open: int


class CapaMonth(BaseModel):
    month: str
    created: int
    resolved: int


class AssessmentMonth(BaseModel):
    month: str
    count: int


class BodyRow(CountRow):
    region: str | None = None


class BodyMap(BaseModel):
    regions: dict[str, int]
    off_picture: list[BodyRow]


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


class MonthChange(BaseModel):
    """UAT 14: this month's figure and last month's, for the arrow."""
    month: str
    previous: str
    this: dict[str, int]
    last: dict[str, int]


class DashboardOut(BaseModel):
    headline: Headline
    month_change: MonthChange
    medical_events: list[MonthRow]
    near_misses: list[NearMissRow]
    body_parts: list[BodyRow]
    body_map: BodyMap
    injured_departments: list[CountRow]
    injured_locations: list[CountRow]
    resolution: list[ResolutionMonth]
    capa: list[CapaMonth]
    assessments: list[AssessmentMonth]
    jhsc: Jhsc
    # The colour each figure has earned under HSE's numbers, None for none.
    tones: dict[str, str | None]
    chart_styles: dict[str, str]


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    year: int | None = Query(default=None, ge=2000, le=2100),
    hours_worked: float | None = Query(default=None, ge=0),
):
    year = year or plant_today().year
    headline = await svc.headline(db, year=year, hours_worked=hours_worked)
    resolution = await svc.resolution_by_month(db, year=year)
    body = await svc.body_parts(db, year=year)
    config = await db.get(EhsConfig, 1)
    thresholds = config.dashboard_thresholds if config else {}

    # The scorecard's colour is this month's figure — or, looking back at
    # another year, that year's December.
    today = plant_today()
    month = today.month if year == today.year else 12
    current = resolution[month - 1]["average_days"]
    figures = {
        "days_since": headline["days_since_last_incident"],
        "injuries": headline["injuries"], "first_aid": headline["first_aid"],
        "medical_aid": headline["medical"], "lost_time": headline["lost_time"],
        "trir": headline["rir"], "resolution_days": current,
    }
    return DashboardOut(
        headline=headline,
        month_change=await svc.month_on_month(db, year=year),
        medical_events=await svc.medical_events_by_month(db, year=year),
        near_misses=await svc.near_misses_by_month(db, year=year),
        body_parts=body["rows"],
        body_map={"regions": body["regions"], "off_picture": body["off_picture"]},
        injured_departments=await svc.injured_by_department(db, year=year),
        injured_locations=await svc.injured_by_location(db, year=year),
        resolution=resolution,
        capa=await svc.capa_by_month(db, year=year),
        assessments=await svc.assessments_by_month(db, year=year),
        jhsc=await svc.jhsc_attendance(db, year=year),
        tones={k: ds.tone(k, v, thresholds) for k, v in figures.items()},
        chart_styles=ds.chart_styles(config.dashboard_chart_styles if config else {}),
    )


# ── Food safety (UAT 14) ────────────────────────────────────────────────────

class FsMonth(BaseModel):
    month: str
    count: int
    previous: str
    previous_count: int
    change_percent: float | None


class FsMonthRow(BaseModel):
    month: str
    count: int


class FoodSafetyOut(BaseModel):
    year: int
    total: int
    month: FsMonth
    bands: dict[str, int]
    not_assessed: int
    investigations: dict[str, int]
    capa: dict[str, int]
    by_month: list[FsMonthRow]
    average_days: float | None
    resolved: int


@router.get("/food-safety", response_model=FoodSafetyOut)
async def food_safety(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    year: int | None = Query(default=None, ge=2000, le=2100),
):
    """★ UAT 14: "beside the 'HSE Dashboard' … create a similar title of 'Food
    Safety'", with the figures Kirk listed. Each one is counted so that the
    list it opens holds exactly the records it counted."""
    return FoodSafetyOut(**await svc.food_safety(db, year=year or plant_today().year))


# ── The chart by supervisor ─────────────────────────────────────────────────

class SupervisorColumn(BaseModel):
    supervisor_id: str | None
    name: str
    near_miss: int
    hazard_assessment: int
    toolbox_talk: int


class SupervisorActivityOut(BaseModel):
    month: str
    rows: list[SupervisorColumn]
    unassigned: SupervisorColumn | None


@router.get("/supervisor-activity", response_model=SupervisorActivityOut)
async def supervisor_activity(
    db: SessionDep, user: CanManageSettings,  # noqa: ARG001
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    """One month, by supervisor. HSE only.

    ★ Its own gate rather than the dashboard's. Everybody who may read
    incidents may open the dashboard, and this is a figure about named
    supervisors' teams. Whether a supervisor may see the plant's or only their
    own is the open question about staff files, and until it is answered they
    see neither.
    """
    if month:
        y, m = (int(x) for x in month.split("-"))
    else:
        today = plant_today()
        y, m = today.year, today.month
    return await svc.supervisor_activity(db, year=y, month=m)


# ── What HSE sets for everybody ─────────────────────────────────────────────

class MetricOut(BaseModel):
    key: str
    label: str
    higher_is_better: bool


class ChartOut(BaseModel):
    key: str
    label: str
    styles: list[str]


class RegionOut(BaseModel):
    key: str
    label: str


class DashboardSettingsOut(BaseModel):
    thresholds: dict[str, dict]
    chart_styles: dict[str, str]
    metrics: list[MetricOut]
    charts: list[ChartOut]
    regions: list[RegionOut]
    due_bands: dict[str, int]


class DashboardSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thresholds: dict[str, dict] | None = None
    chart_styles: dict[str, str] | None = None
    due_bands: dict | None = None


def _settings_out(config: EhsConfig | None) -> DashboardSettingsOut:
    return DashboardSettingsOut(
        thresholds=(config.dashboard_thresholds if config else {}) or {},
        chart_styles=ds.chart_styles(config.dashboard_chart_styles if config else {}),
        metrics=[MetricOut(key=m.key, label=m.label, higher_is_better=m.higher_is_better)
                 for m in ds.METRICS],
        charts=[ChartOut(key=k, label=label, styles=list(styles))
                for k, (label, styles) in ds.CHARTS.items()],
        regions=[RegionOut(key=k, label=label) for k, label in body_map.REGIONS],
        due_bands=ds.due_bands(config.due_bands if config else None),
    )


@router.get("/settings", response_model=DashboardSettingsOut)
async def get_settings(db: SessionDep, user: CanReadIncident):  # noqa: ARG001
    """Readable by whoever can open the dashboard: the page says what the
    colours mean, and it needs the numbers to say it."""
    return _settings_out(await db.get(EhsConfig, 1))


@router.put("/settings", response_model=DashboardSettingsOut)
async def put_settings(payload: DashboardSettingsIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Replaces what is sent; leaves alone what is not."""
    from app.api.v1.settings import _config_row
    config = await _config_row(db)
    if payload.thresholds is not None:
        config.dashboard_thresholds = ds.clean_thresholds(payload.thresholds)
    if payload.chart_styles is not None:
        config.dashboard_chart_styles = ds.clean_chart_styles(payload.chart_styles)
    if payload.due_bands is not None:
        config.due_bands = ds.clean_due_bands(payload.due_bands)
    await db.flush()
    return _settings_out(config)


# ── Which panels a person wants, and in what order ──────────────────────────

# The library. Kept on the server so that a panel added to the product appears
# in everybody's chooser without each of them being handed a new frontend.
PANELS: tuple[tuple[str, str], ...] = (
    ("injuries_by_month", "Injuries by month"),
    ("near_misses", "Near misses and good catches"),
    ("resolution", "Average days to resolve incidents"),
    ("capa", "Corrective actions raised and closed"),
    ("assessments", "Hazard assessments completed"),
    ("supervisor_activity", "Near misses, assessments and talks by supervisor"),
    ("injured_locations", "Where injuries are happening"),
    ("injured_departments", "Injured by department"),
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


def _arranged(row: DashboardLayout | None) -> list[str]:
    """What a person sees: their saved order, plus any panel added since.

    ★ A saved list used to be final, so a panel added to the product stayed
    invisible to everybody who had ever rearranged theirs. `known` is the
    library as it was when they saved; whatever is new since is appended, and
    whatever they took off stays off.
    """
    if row is None or not row.panels:
        return DEFAULT_PANELS
    chosen = [p for p in row.panels if p in DEFAULT_PANELS]
    known = set(row.known or [])
    return chosen + [p for p in DEFAULT_PANELS if p not in known and p not in chosen]


@router.get("/layout", response_model=LayoutOut)
async def get_layout(db: SessionDep, user: CanReadIncident):
    """Mine. Nothing stored means the default order, which is also what a
    person who has never touched this gets."""
    row = await db.get(DashboardLayout, _user_id(user))
    return LayoutOut(
        panels=_arranged(row),
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
        row = DashboardLayout(user_id=_user_id(user), panels=wanted, known=DEFAULT_PANELS)
        db.add(row)
    else:
        row.panels = wanted
        row.known = DEFAULT_PANELS
    await db.flush()
    return LayoutOut(
        panels=_arranged(row),
        available=[PanelOut(key=k, label=l) for k, l in PANELS],
    )
