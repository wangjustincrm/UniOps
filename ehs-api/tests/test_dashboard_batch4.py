"""Round 10, batch 4: the dashboard HSE asked for.

Round 9 (q9-other) gave the layout — five boxes, colours by threshold, chart
styles, a body picture, monthly scorecards, a chart by supervisor. Round 10
answered who sets what: "HSE will set the numbers once for everybody under
Settings", chart style by HSE too, supervisors from the org chart in the
Portal.

Each "this one counts" sits next to a "this one does not", so a test cannot be
passed by a figure that counts nothing or everything.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.action import Action
from app.models.config import EhsConfig
from app.models.dashboard_layout import DashboardLayout
from app.models.incident import Incident, IncidentPerson
from app.models.risk import RiskAssessment
from app.models.toolbox import TbtDelivery, ToolboxTalk
from app.services import dashboard_settings as ds
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user

ONTARIO = ZoneInfo("America/Toronto")
TODAY = plant_today()


def _at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    """A moment on a plant day, as the instant stored."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ONTARIO).astimezone(timezone.utc)


def _incident(db, *, kind="medical", status="submitted", injury=None, occurred=None,
              submitted=None, closed=None, reporter=None, anonymous=False, cost=None):
    occurred = occurred or _at(TODAY)
    row = Incident(
        id=uuid.uuid4(), incident_no=f"T-{uuid.uuid4().hex[:10]}", form_kind=kind,
        title="Test", status=status, occurred_at=occurred, injury_class=injury,
        submitted_at=submitted or (None if status == "draft" else occurred),
        closed_at=closed, reported_by=reporter, is_anonymous=anonymous, cost=cost)
    db.add(row)
    return row


async def _supervised(test_engine, supervisor_id):
    """Somebody who reports to `supervisor_id` in the Portal's org chart."""
    person = await make_user(test_engine, role="requester")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("update users set supervisor_id = :s where id = :u"),
                        {"s": supervisor_id, "u": person.id})
        await s.commit()
    return person


@pytest.fixture
async def supervisor(test_engine, db_session):
    """Can open the dashboard (ehs.incident.read) but cannot manage settings."""
    user = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        yield user, c


@pytest.fixture
async def body_list(db_session):
    from app.models.vocabulary import Vocabulary
    db_session.add_all([Vocabulary(code="body_part", name="Body Part"),
                        Vocabulary(code="hazard", name="Hazard", is_hierarchical=True)])
    await db_session.flush()


async def _dash(client, **params):
    got = await client.get("/api/v1/dashboard", params=params)
    assert got.status_code == 200, got.text
    return got.json()


# ── Colours: which way is better belongs to the figure ──────────────────────

class TestTone:
    T = {"days_since": {"good": 15, "bad": 5}, "lost_time": {"good": 1, "bad": 3}}

    def test_kirks_example_with_its_strict_edges(self):
        # "greater than 15 … green … less than 5 … red … yellow in between"
        assert ds.tone("days_since", 16, self.T) == "good"
        assert ds.tone("days_since", 15, self.T) == "warn"
        assert ds.tone("days_since", 5, self.T) == "warn"
        assert ds.tone("days_since", 4, self.T) == "bad"

    def test_fewer_is_better_reads_the_other_way(self):
        assert ds.tone("lost_time", 0, self.T) == "good"
        assert ds.tone("lost_time", 2, self.T) == "warn"
        assert ds.tone("lost_time", 4, self.T) == "bad"

    def test_no_numbers_or_no_value_is_no_colour(self):
        assert ds.tone("first_aid", 3, self.T) is None
        assert ds.tone("days_since", None, self.T) is None

    def test_a_pair_against_its_figure_is_refused(self):
        with pytest.raises(HTTPException) as e:
            ds.clean_thresholds({"days_since": {"good": 5, "bad": 15},
                                 "lost_time": {"good": 3, "bad": 1},
                                 "first_aid": {"good": 2}})
        problems = e.value.detail["problems"]
        assert len(problems) == 3, problems

    def test_a_consistent_pair_is_kept_and_an_empty_one_clears(self):
        assert ds.clean_thresholds({"days_since": {"good": 15, "bad": 5},
                                    "lost_time": {"good": None, "bad": None}}) == {
            "days_since": {"good": 15, "bad": 5}}


async def test_hse_sets_the_numbers_and_the_dashboard_wears_them(db_session, hse_manager, supervisor):
    db_session.add(EhsConfig(id=1))
    await db_session.flush()
    _, hse = hse_manager
    _, employee = supervisor

    refused = await employee.put("/api/v1/dashboard/settings",
                                 json={"thresholds": {"lost_time": {"good": 1, "bad": 3}}})
    assert refused.status_code == 403
    backwards = await hse.put("/api/v1/dashboard/settings",
                              json={"thresholds": {"days_since": {"good": 5, "bad": 15}}})
    assert backwards.status_code == 422, backwards.text

    # A lost-time injury this year, and the numbers that make one red.
    _incident(db_session, injury="lost_time", occurred=_at(TODAY - timedelta(days=1)))
    await db_session.flush()
    saved = await hse.put("/api/v1/dashboard/settings", json={
        "thresholds": {"lost_time": {"good": 0, "bad": 0}},
        "chart_styles": {"injured_locations": "circle"}})
    assert saved.status_code == 200, saved.text
    seen = await _dash(employee)
    assert seen["tones"]["lost_time"] == "bad"
    assert seen["tones"]["first_aid"] is None, "no numbers set, no colour"
    assert seen["chart_styles"] == {"injured_locations": "circle",
                                    "injured_departments": "bar", "body_parts": "body"}
    bad_style = await hse.put("/api/v1/dashboard/settings",
                              json={"chart_styles": {"injured_locations": "body"}})
    assert bad_style.status_code == 422, "only the body chart can be a body"


# ── The five boxes ──────────────────────────────────────────────────────────

async def test_days_since_is_null_rather_than_zero_with_no_injury(db_session, hse_manager):
    _, hse = hse_manager
    # A draft injury and an incident with no injury: neither is a medical incident.
    _incident(db_session, injury="first_aid", status="draft")
    _incident(db_session, kind="near_miss")
    await db_session.flush()
    head = (await _dash(hse))["headline"]
    if head["injuries"] == 0:
        assert head["days_since_last_incident"] is None
    # And once there is one, it counts plant days from it.
    _incident(db_session, injury="first_aid", occurred=_at(TODAY - timedelta(days=3), 23, 30))
    await db_session.flush()
    assert (await _dash(hse))["headline"]["days_since_last_incident"] == 3


async def test_the_cost_says_how_many_incidents_carry_one(db_session, hse_manager):
    _, hse = hse_manager
    before = (await _dash(hse))["headline"]
    _incident(db_session, injury="first_aid", cost=120)
    _incident(db_session, injury="first_aid")
    await db_session.flush()
    after = (await _dash(hse))["headline"]
    assert after["incidents"] == before["incidents"] + 2
    assert after["cost_known_count"] == before["cost_known_count"] + 1


# ── Rates: one window for both halves ───────────────────────────────────────

async def test_reports_rates_count_this_years_incidents_only(db_session, hse_manager):
    """★ With no dates the incidents were counted over all time and divided by
    this year's hours."""
    _, hse = hse_manager
    before = (await hse.get("/api/v1/reports/injury-rates",
                            params={"hours_worked": 200000})).json()
    _incident(db_session, injury="lost_time", occurred=_at(date(TODAY.year - 2, 6, 1)))
    await db_session.flush()
    old = (await hse.get("/api/v1/reports/injury-rates", params={"hours_worked": 200000})).json()
    assert old["lost_time"] == before["lost_time"], "two years ago is not this year"
    _incident(db_session, injury="lost_time", occurred=_at(TODAY))
    await db_session.flush()
    now = (await hse.get("/api/v1/reports/injury-rates", params={"hours_worked": 200000})).json()
    assert now["lost_time"] == before["lost_time"] + 1
    dash = (await _dash(hse, hours_worked=200000))["headline"]
    assert dash["lost_time_rate"] == now["lost_time_rate"], "one sum, two screens"


async def test_a_date_edge_is_the_plants_day(db_session, hse_manager):
    """9pm on 31 December in Ontario is already next year in UTC."""
    _, hse = hse_manager
    y = TODAY.year - 1
    params = {"hours_worked": 200000, "date_from": f"{y}-01-01", "date_to": f"{y + 1}-01-01"}
    before = (await hse.get("/api/v1/reports/injury-rates", params=params)).json()
    _incident(db_session, injury="lost_time", occurred=_at(date(y, 12, 31), 21))
    _incident(db_session, injury="lost_time", occurred=_at(date(y + 1, 1, 1), 1))
    await db_session.flush()
    after = (await hse.get("/api/v1/reports/injury-rates", params=params)).json()
    assert after["lost_time"] == before["lost_time"] + 1


async def test_only_an_end_date_means_that_years_start(db_session, hse_manager):
    _, hse = hse_manager
    y = TODAY.year - 1
    params = {"hours_worked": 200000, "date_to": f"{y}-07-01"}
    before = (await hse.get("/api/v1/reports/injury-rates", params=params)).json()
    _incident(db_session, injury="lost_time", occurred=_at(date(y, 3, 3)))
    _incident(db_session, injury="lost_time", occurred=_at(date(y - 1, 12, 3)))
    await db_session.flush()
    after = (await hse.get("/api/v1/reports/injury-rates", params=params)).json()
    assert after["lost_time"] == before["lost_time"] + 1


# ── Monthly scorecards ──────────────────────────────────────────────────────

async def test_resolution_averages_the_closed_and_counts_the_open(db_session, hse_manager):
    _, hse = hse_manager
    y = TODAY.year - 1  # a whole past year, so "this month" is not in play
    m = 3
    before = (await _dash(hse, year=y))["resolution"][m - 1]
    start = _at(date(y, m, 10), 9)
    _incident(db_session, status="closed", occurred=start, submitted=start,
              closed=start + timedelta(days=4))
    _incident(db_session, status="closed", occurred=start, submitted=start,
              closed=start + timedelta(days=2))
    _incident(db_session, status="under_investigation", occurred=start)
    _incident(db_session, status="draft", occurred=start)
    await db_session.flush()
    got = (await _dash(hse, year=y))["resolution"][m - 1]
    assert got["closed"] == before["closed"] + 2
    assert got["still_open"] == before["still_open"] + 1, "a draft is not open"
    if before["closed"] == 0:
        assert got["average_days"] == 3.0, "the open one is not a zero-day resolution"


async def test_capa_raised_and_closed_by_month(db_session, hse_manager):
    _, hse = hse_manager
    y = TODAY.year - 1
    before = (await _dash(hse, year=y))["capa"]
    for closed in (None, _at(date(y, 5, 2))):
        db_session.add(Action(
            id=uuid.uuid4(), action_no=f"A-{uuid.uuid4().hex[:8]}", source_type="incident",
            title="Fix it", due_date=date(y, 5, 30),
            status="closed" if closed else "open", closed_at=closed,
            created_at=_at(date(y, 4, 20))))
    await db_session.flush()
    after = (await _dash(hse, year=y))["capa"]
    assert after[3]["created"] == before[3]["created"] + 2
    assert after[4]["resolved"] == before[4]["resolved"] + 1
    assert after[3]["resolved"] == before[3]["resolved"]


# ── The chart by supervisor ─────────────────────────────────────────────────

async def test_by_supervisor_is_hse_only(hse_manager, supervisor):
    """The denial is somebody who can open the dashboard itself."""
    _, hse = hse_manager
    _, employee = supervisor
    assert (await employee.get("/api/v1/dashboard")).status_code == 200
    assert (await employee.get("/api/v1/dashboard/supervisor-activity")).status_code == 403
    assert (await hse.get("/api/v1/dashboard/supervisor-activity")).status_code == 200


async def test_each_record_goes_to_the_right_column(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    boss = await make_user(test_engine, role="area_supervisor", full_name="Sam Boss")
    member = await _supervised(test_engine, boss.id)
    y, m = TODAY.year - 1, 7
    month = f"{y}-{m:02d}"
    before = (await hse.get("/api/v1/dashboard/supervisor-activity",
                            params={"month": month})).json()
    inside = _at(date(y, m, 31), 22)          # 10pm on 31 July here; already August in UTC
    outside = _at(date(y, m + 1, 1), 0, 30)   # half past midnight on 1 August here

    _incident(db_session, kind="near_miss", reporter=member.id, occurred=inside)
    _incident(db_session, kind="near_miss", reporter=member.id, occurred=outside)
    _incident(db_session, kind="near_miss", reporter=member.id, occurred=inside, status="draft")
    # ★ Anonymous: never in the reporter's supervisor's column.
    _incident(db_session, kind="near_miss", reporter=member.id, occurred=inside, anonymous=True)
    db_session.add(RiskAssessment(
        id=uuid.uuid4(), likelihood_code="B", severity_level=2, rating="medium",
        assessed_by=member.id, assessed_at=inside))
    talk = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-{uuid.uuid4().hex[:6]}",
                       title="Ladders", period_month=date(y, m, 1))
    db_session.add(talk)
    await db_session.flush()
    db_session.add(TbtDelivery(id=uuid.uuid4(), talk_id=talk.id, supervisor_id=boss.id,
                               supervisor_name="Sam Boss", status="delivered",
                               held_at=_at(date(y, m, 15))))
    later = ToolboxTalk(id=uuid.uuid4(), talk_no=f"TBT-{uuid.uuid4().hex[:6]}",
                        title="Forklifts", period_month=date(y, m, 1))
    db_session.add(later)
    await db_session.flush()
    db_session.add(TbtDelivery(id=uuid.uuid4(), talk_id=later.id, supervisor_id=boss.id,
                               supervisor_name="Sam Boss", status="pending"))
    await db_session.flush()

    got = (await hse.get("/api/v1/dashboard/supervisor-activity",
                         params={"month": month})).json()
    col = next(r for r in got["rows"] if r["supervisor_id"] == str(boss.id))
    assert (col["name"], col["near_miss"], col["hazard_assessment"], col["toolbox_talk"]) == (
        "Sam Boss", 1, 1, 1)
    was = (before["unassigned"] or {}).get("near_miss", 0)
    assert got["unassigned"]["near_miss"] == was + 1, "the anonymous one, unassigned"


async def test_a_month_must_be_a_month(hse_manager):
    _, hse = hse_manager
    assert (await hse.get("/api/v1/dashboard/supervisor-activity",
                          params={"month": "2026-13"})).status_code == 422


# ── The body picture ────────────────────────────────────────────────────────

async def _body_part(hse, label, code=None, attrs=None):
    made = await hse.post("/api/v1/settings/vocabularies/body_part/items",
                          json={"label": label, **({"code": code} if code else {}),
                                **({"attrs": attrs} if attrs else {})})
    assert made.status_code in (200, 201), made.text
    return made.json()


async def test_the_figure_shades_regions_and_names_what_it_cannot(db_session, hse_manager, body_list):
    _, hse = hse_manager
    y = TODAY.year - 1
    seeded = await _body_part(hse, "Toe test", code="toe")             # placed by its code
    placed = await _body_part(hse, "Thumb test", attrs={"body_region": "hand"})
    unplaced = await _body_part(hse, "Whole body test")
    finger = await _body_part(hse, "Finger test", code="finger")
    before = (await _dash(hse, year=y))["body_map"]["regions"]

    two_on_one_hand = _incident(db_session, injury="first_aid", occurred=_at(date(y, 2, 2)))
    other = _incident(db_session, injury="first_aid", occurred=_at(date(y, 2, 3)))
    await db_session.flush()
    for incident, part in ((two_on_one_hand, placed), (two_on_one_hand, finger),
                           (other, seeded), (other, unplaced)):
        db_session.add(IncidentPerson(id=uuid.uuid4(), incident_id=incident.id, role="injured",
                                      person_name="X", body_part_id=uuid.UUID(part["id"]),
                                      body_part_label=part["label"]))
    db_session.add(IncidentPerson(id=uuid.uuid4(), incident_id=other.id, role="injured",
                                  person_name="Y", body_part_label="typed in by hand"))
    await db_session.flush()

    got = (await _dash(hse, year=y))
    regions = got["body_map"]["regions"]
    assert regions.get("hand", 0) == before.get("hand", 0) + 1, "one incident, however many fingers"
    assert regions.get("foot", 0) == before.get("foot", 0) + 1
    off = {r["label"] for r in got["body_map"]["off_picture"]}
    assert {"Whole body test", "typed in by hand"} <= off
    assert "Thumb test" not in off


async def test_only_a_real_region_on_a_body_part(hse_manager, body_list):
    _, hse = hse_manager
    item = await _body_part(hse, "Region check")
    path = f"/api/v1/settings/vocabularies/body_part/items/{item['id']}"
    assert (await hse.patch(path, json={"attrs": {"body_region": "tail"}})).status_code == 422
    assert (await hse.patch(path, json={"attrs": {"body_region": "none"}})).status_code == 200
    assert (await hse.patch(path, json={"attrs": {"body_region": "knee"}})).status_code == 200
    other = await hse.post("/api/v1/settings/vocabularies/hazard/items",
                           json={"label": "Region on a hazard", "attrs": {"body_region": "knee"}})
    assert other.status_code == 422


# ── Panels added later reach people who saved a layout ──────────────────────

async def test_a_new_panel_is_offered_and_a_removed_one_stays_off(db_session, hse_manager):
    user, hse = hse_manager
    before_this_batch = ["injuries_by_month", "near_misses", "participation",
                         "injured_locations", "injured_departments", "body_parts",
                         "jhsc_attendance"]
    # Saved before this batch, with JHSC attendance taken off.
    db_session.add(DashboardLayout(user_id=user.id, panels=before_this_batch[:-1],
                                   known=before_this_batch))
    await db_session.flush()
    got = (await hse.get("/api/v1/dashboard/layout")).json()["panels"]
    assert "jhsc_attendance" not in got, "what they took off stays off"
    assert "participation" not in got, "retired"
    assert got[:2] == ["injuries_by_month", "near_misses"], "their order kept"
    assert {"resolution", "capa", "assessments", "supervisor_activity"} <= set(got)

    # Saving again stamps the library: a panel they now untick stays off.
    saved = await hse.put("/api/v1/dashboard/layout",
                          json={"panels": [p for p in got if p != "capa"]})
    assert "capa" not in saved.json()["panels"]
