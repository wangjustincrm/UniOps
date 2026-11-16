"""Review of UAT 13/14 (uniops-26): R1, R2, R5, R7, R8, R11, R12, R14, R15, R17.
Each test fails on the code before its fix."""
import uuid

import pytest
from sqlalchemy import text

from app.services import email_templates as et
from tests.conftest import authed_client, make_token, make_user
from tests.test_food_safety_r13b import _food_near_miss
from tests.test_incident_types_r12 import _food, _near_miss


@pytest.fixture(autouse=True)
async def _config(db_session):
    from app.models.config import EhsConfig
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()


# ── R1 ──────────────────────────────────────────────────────────────────────
async def test_r1_the_section_b_rule_reaches_only_incidents_the_supervisor_can_open(
        hse_manager, test_engine, db_session):
    _, hse = hse_manager
    assert (await hse.put("/api/v1/settings/config",
                          json={"supervisors_see_section_b": True})).status_code == 200
    sup = await make_user(test_engine, role="supervisor")     # no ehs.incident.read
    theirs_not = (await hse.post("/api/v1/incidents", json=_near_miss("near_miss"))).json()
    await hse.post(f"/api/v1/incidents/{theirs_not['id']}/submit")
    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        mine = (await s.post("/api/v1/incidents", json=_near_miss("near_miss"))).json()
        await s.post(f"/api/v1/incidents/{mine['id']}/submit")
        assert (await s.get(f"/api/v1/incidents/{theirs_not['id']}/medical")).status_code == 403
        assert (await s.put(f"/api/v1/incidents/{theirs_not['id']}/medical", json={})).status_code == 403
        assert (await s.get(f"/api/v1/incidents/{mine['id']}/medical")).status_code == 200
    # HSE, holding medical.read, unchanged.
    assert (await hse.get(f"/api/v1/incidents/{theirs_not['id']}/medical")).status_code == 200
    await hse.put("/api/v1/settings/config", json={"supervisors_see_section_b": False})
    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        assert (await s.get(f"/api/v1/incidents/{mine['id']}/medical")).status_code == 403


# ── R2 ──────────────────────────────────────────────────────────────────────
async def test_r2_an_answered_recommendation_sent_again_owes_no_reply(hse_manager):
    _, hse = hse_manager
    from app.services.day_range import plant_today
    meeting = (await hse.post("/api/v1/jhsc/meetings", json={
        "held_on": plant_today().isoformat(), "title": "Monthly"})).json()
    rec = (await hse.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                          json={"recommendation": "Fit a guard"})).json()
    body = {"meeting_id": meeting["id"], "recommendation": "Fit a guard", "recommendation_id": rec["id"]}
    before = (await hse.post("/api/v1/jhsc/recommendations/preview", json=body)).json()
    assert "response is owed" in before["subject"]
    await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/respond",
                   json={"response": "Guard fitted on 12 November", "outcome": "accepted"})
    after = (await hse.post("/api/v1/jhsc/recommendations/preview", json=body)).json()
    assert "already answered" in after["subject"] and "owed" not in after["subject"]
    assert "Guard fitted on 12 November" in after["body"] and "s.9(20)" not in after["body"]


# ── R5 ──────────────────────────────────────────────────────────────────────
async def test_r5_who_recorded_a_filing_is_kept(worker, hse_manager, db_session):
    from tests.test_first_aid_and_wsib import _lost_time_incident
    _, w = worker
    me, hse = hse_manager
    incident = await _lost_time_incident(w, hse)
    got = await hse.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                         json={"confirmation_number": "WSIB-1"})
    assert got.status_code == 200, got.text
    name = (await db_session.execute(text(
        "select satisfied_by_name from ehs_statutory_deadlines where source_id = :i and kind = 'wsib_form7'"),
        {"i": incident["id"]})).scalar()
    assert name and name == me.full_name


# ── R7 ──────────────────────────────────────────────────────────────────────
async def test_r7_an_anonymous_reporters_own_assessment_is_not_signed(
        hse_manager, test_engine, db_session):
    _, hse = hse_manager
    await _food(db_session)
    coord = await make_user(test_engine, role="ehs_coordinator", full_name="Cora Quiet")
    async with authed_client(make_token(coord.id, coord.role), db_session) as c:
        made = (await c.post("/api/v1/incidents", json=_near_miss(
            "near_miss_food_safety", is_anonymous=True))).json()
        await c.post(f"/api/v1/incidents/{made['id']}/submit")
        assert (await c.post(f"/api/v1/incidents/{made['id']}/food-safety-assessments",
                             json={"probability": 2, "severity": 2, "detectability": 2})).status_code == 201
    sup = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        rows = (await s.get(f"/api/v1/incidents/{made['id']}/food-safety-assessments")).json()
        assert rows[0]["assessed_by_name"] is None
        reg = (await s.get("/api/v1/food-safety/register", params={"q": made["incident_no"]})).json()
        assert reg["items"][0]["assessed_by_name"] is None
    # HSE learns who it was, as for every anonymous report.
    rows = (await hse.get(f"/api/v1/incidents/{made['id']}/food-safety-assessments")).json()
    assert rows[0]["assessed_by_name"] == "Cora Quiet"


# ── R8 ──────────────────────────────────────────────────────────────────────
async def test_r8_a_department_keeps_its_recorded_name_when_saved_again(hse_manager, db_session):
    from app.models.mirrors import Department
    _, hse = hse_manager
    dept = Department(id=uuid.uuid4(), code=f"D{uuid.uuid4().hex[:4]}", name="Sanitation", is_active=True)
    gone = Department(id=uuid.uuid4(), code=f"D{uuid.uuid4().hex[:4]}", name="Old crew", is_active=False)
    db_session.add_all([dept, gone])
    await db_session.commit()
    food = await _food_near_miss(hse, db_session)
    url = f"/api/v1/incidents/{food['id']}/causes"
    cause = {"cause_type": "immediate", "label": "Lid left off",
             "completed_by_department_id": str(dept.id), "completed_on": "2026-11-10"}
    assert (await hse.put(url, json={"causes": [cause]})).status_code == 200
    dept.name = "Sanitation & Hygiene"
    await db_session.commit()
    again = await hse.put(url, json={"causes": [cause, {"cause_type": "immediate", "label": "Another"}]})
    assert again.status_code == 200, again.text
    kept = next(c for c in again.json()["immediate"] if c["label"] == "Lid left off")
    assert kept["completed_by_department_name"] == "Sanitation"
    # A department no longer in use cannot be newly picked.
    retired = await hse.put(url, json={"causes": [
        {**cause, "completed_by_department_id": str(gone.id)}]})
    assert retired.status_code == 422


# ── R11 ─────────────────────────────────────────────────────────────────────
def test_r11_a_chronology_time_is_a_date_and_time():
    from pydantic import ValidationError
    from app.schemas.investigation import ChronologyRow
    assert ChronologyRow(at="2026-11-16T09:40").at == "2026-11-16T09:40"
    assert ChronologyRow(at="2026-11-16T09:40:30").at
    with pytest.raises(ValidationError):
        ChronologyRow(at="tomorrow morning")


# ── R12, R15 ────────────────────────────────────────────────────────────────
def test_r12_a_placeholder_is_a_name_and_nothing_else():
    t = et.TEMPLATES["action_owner_told"]
    assert et.problems("action_owner_told", t.subject, t.body) == []
    found = et.problems("action_owner_told", t.subject, "Hi {owner_name:>99999999}")
    assert found and "nothing else" in found[0]
    assert et.problems("action_owner_told", t.subject, "Hi {owner_name!r}")


async def test_r15_a_subject_with_line_breaks_is_one_line(hse_manager, db_session):
    _, hse = hse_manager
    assert (await hse.put("/api/v1/settings/email-templates/feedback_left", json={
        "subject": "Feedback: {comment}", "body": "{comment}"})).status_code == 200
    subject, _ = await et.compose(db_session, "feedback_left",
                                  {"who": "Ken", "page": "", "comment": "Line one\nline two"})
    assert subject == "Feedback: Line one line two"


# ── R14 ─────────────────────────────────────────────────────────────────────
async def test_r14_the_register_takes_the_dashboards_year_and_leaves_drafts_out(hse_manager, db_session):
    from datetime import datetime, timezone
    from app.models.food_safety import FoodSafetyAssessment
    from app.models.incident import Incident
    _, hse = hse_manager
    await _food(db_session)
    ids = []
    for when, status in ((datetime(2043, 3, 1, 12, tzinfo=timezone.utc), "submitted"),
                         (datetime(2044, 3, 1, 12, tzinfo=timezone.utc), "submitted"),
                         (datetime(2043, 4, 1, 12, tzinfo=timezone.utc), "draft")):
        i = Incident(id=uuid.uuid4(), incident_no=f"INC-R14-{uuid.uuid4().hex[:6]}", form_kind="near_miss",
                     title="r14", status=status, occurred_at=when,
                     extra={"incident_type": "near_miss_food_safety"})
        db_session.add(i)
        ids.append(i.id)
    await db_session.flush()
    for i in ids:
        db_session.add(FoodSafetyAssessment(id=uuid.uuid4(), incident_id=i, probability=1, severity=1,
                                            detectability=1, rpn=46, ccp_failure=True, band="critical",
                                            assessed_at=datetime(2044, 5, 1, tzinfo=timezone.utc)))
    await db_session.flush()
    got = (await hse.get("/api/v1/food-safety/register", params={
        "q": "INC-R14", "band": "critical",
        "occurred_from": "2043-01-01", "occurred_to": "2043-12-31"})).json()
    assert got["total"] == 1
    every = (await hse.get("/api/v1/food-safety/register", params={"q": "INC-R14"})).json()
    assert every["total"] == 2          # the draft never


# ── R17 ─────────────────────────────────────────────────────────────────────
async def test_r17_a_copy_needs_a_title(hse_manager, db_session):
    from tests.test_quiz_roster import _quiz
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    got = await hse.post(f"/api/v1/quizzes/{quiz.id}/copy", json={"title": "   "})
    assert got.status_code == 422
