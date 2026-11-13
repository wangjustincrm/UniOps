"""UAT 13, first batch of fixes (R13-A)."""
import uuid

from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user


async def _reply_tasks(db_session, user) -> list:
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        return [t for t in (await c.get("/api/v1/actions/mine/tasks")).json()
                if "recommendation" in t["title"].lower()]


async def test_sending_an_answered_recommendation_opens_no_reply_task(
        hse_manager, test_engine, db_session):
    """Ken was sent JHSC-2026-0001 #2 weeks after it was answered and got a
    reply task he could never complete. Sending one not yet answered still
    asks for a reply (the positive half)."""
    _, client = hse_manager
    ken = await make_user(test_engine, full_name="Ken Boucher")
    meeting = (await client.post("/api/v1/jhsc/meetings", json={
        "held_on": plant_today().isoformat(), "title": "Monthly"})).json()
    rec = (await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                             json={"recommendation": "Fit a guard"})).json()

    sent = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/send",
                             json={"user_ids": [str(ken.id)]})
    assert sent.status_code == 200, sent.text
    assert len(await _reply_tasks(db_session, ken)) == 1

    answered = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/respond",
                                 json={"response": "Guard fitted", "outcome": "accepted"})
    assert answered.status_code == 200, answered.text
    assert await _reply_tasks(db_session, ken) == []

    again = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/send",
                              json={"user_ids": [str(ken.id)]})
    assert again.status_code == 200, again.text
    assert await _reply_tasks(db_session, ken) == []


async def test_each_why_is_a_question_and_its_answer(hse_manager, db_session):
    """TC-06: "add a field under the why question to allow for the answer to be
    entered." A food-safety investigation counts the answers, not the questions."""
    from tests.test_incident_types_r12 import _food, _near_miss
    _, hse = hse_manager
    await _food(db_session)
    made = (await hse.post("/api/v1/incidents", json=_near_miss("near_miss_food_safety"))).json()
    await hse.post(f"/api/v1/incidents/{made['id']}/submit")
    whys = [{"question": f"Why {n}?", "answer": f"Because {n}"} for n in range(1, 5)]
    whys.append({"question": "Why was the lid left off?", "answer": ""})
    put = await hse.put(f"/api/v1/incidents/{made['id']}/investigation",
                        json={"investigator_id": None, "five_whys": whys})
    assert put.status_code == 200, put.text
    got = (await hse.get(f"/api/v1/incidents/{made['id']}/investigation")).json()
    assert got["five_whys"][4] == {"question": "Why was the lid left off?", "answer": ""}

    sig = {"signature": "data:image/png;base64,iVBORw0KGgo="}
    refused = await hse.post(f"/api/v1/incidents/{made['id']}/investigation/sign", json=sig)
    assert refused.status_code == 422 and "4 of 5" in refused.text, refused.text
    whys[4]["answer"] = "Nobody checks it at changeover"
    await hse.put(f"/api/v1/incidents/{made['id']}/investigation",
                  json={"investigator_id": None, "five_whys": whys})
    assert (await hse.post(f"/api/v1/incidents/{made['id']}/investigation/sign",
                           json=sig)).status_code == 200


async def test_supervisors_see_section_b_only_when_hse_turn_it_on(
        hse_manager, worker, test_engine, db_session):
    """Question E: off by default, a rule to turn on. HSE always; an ordinary
    employee never."""
    from app.models.config import EhsConfig
    from app.models.incident import Incident
    _, hse = hse_manager
    _, emp = worker
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()
    from tests.test_incident_types_r12 import _near_miss
    made = (await hse.post("/api/v1/incidents", json=_near_miss("near_miss"))).json()
    await hse.post(f"/api/v1/incidents/{made['id']}/submit")
    incident = await db_session.get(Incident, __import__("uuid").UUID(made["id"]))
    url = f"/api/v1/incidents/{incident.id}/medical"
    sup = await make_user(test_engine, role="area_supervisor")

    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        assert (await s.get("/api/v1/section-b/access")).json() == {"may_read": False}
        assert (await s.get(url)).status_code == 403
    assert (await hse.get("/api/v1/section-b/access")).json() == {"may_read": True}
    assert (await hse.get(url)).status_code == 200

    on = await hse.put("/api/v1/settings/config", json={"supervisors_see_section_b": True})
    assert on.status_code == 200 and on.json()["supervisors_see_section_b"] is True
    async with authed_client(make_token(sup.id, sup.role), db_session) as s:
        assert (await s.get("/api/v1/section-b/access")).json() == {"may_read": True}
        assert (await s.get(url)).status_code == 200
    assert (await emp.get("/api/v1/section-b/access")).json() == {"may_read": False}
    assert (await emp.get(url)).status_code == 403


async def test_a_coordinator_records_the_mol_and_wsib_filings_but_not_that_a_clock_lapses(
        hse_manager, worker, test_engine, db_session):
    """UAT 13: "Allow the coord role to add WSIB and MOL reporting information
    on incidents." Saying a clock does not apply stays HSE's."""
    from datetime import datetime, timedelta, timezone
    _, hse = hse_manager
    injured, wclient = worker
    coord = await make_user(test_engine, role="ehs_coordinator")
    incident = (await hse.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Caught in the case sealer",
        "incident_type": "unconscious", "injury_class": "lost_time",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "description": "Found unconscious beside the sealer.",
        "injured_user_id": str(injured.id)})).json()
    kinds = {d["kind"]: d for d in incident["deadlines"]}
    assert "mol_48h" in kinds and "wsib_form7" in kinds, kinds

    # An ordinary employee may do neither.
    assert (await wclient.post(f"/api/v1/incidents/{incident['id']}/mol-report/filed",
                               json={"reference": "x"})).status_code == 403

    async with authed_client(make_token(coord.id, coord.role), db_session) as c:
        mol = await c.post(f"/api/v1/incidents/{incident['id']}/mol-report/filed",
                           json={"reference": "Emailed to the Kingston office"})
        assert mol.status_code == 200, mol.text
        f7 = await c.post(f"/api/v1/incidents/{incident['id']}/wsib-form7/filed",
                          json={"confirmation_number": "WSIB-2026-1"})
        assert f7.status_code == 200, f7.text
        lapse = await c.post(f"/api/v1/deadlines/{kinds['wsib_form7']['id']}/not-required",
                             json={"reason": "Reclassified as first aid"})
        assert lapse.status_code == 403


async def test_every_incident_has_a_category_in_reports(hse_manager, db_session):
    """UAT 13: "120 incidents that are not categorized which is wrong … Use the
    list in the Settings/Incident types registry as the categories." A type
    with a category of its own is grouped under it; any other type is its own
    category; and the bar opens to exactly its incidents."""
    from tests.test_reporting_search import _area, _counts, _incident
    _, client = hse_manager
    area = _area()
    for kind in ("first_aid", "first_aid", "near_miss"):
        await _incident(db_session, area=area, kind=kind)
    food = await _incident(db_session, area=area, kind="near_miss_food_safety", form="near_miss")
    food.category_label = "Food Safety"
    await _incident(db_session, area=area, kind=None)
    await db_session.flush()

    got = await client.get("/api/v1/reports/group", params={
        "entity": "incident", "dimension": "category", "filter": f"location:{area}"})
    assert got.status_code == 200, got.text
    assert _counts(got.json()) == {"First Aid Injury": 2, "Near Miss": 1,
                                   "Food Safety": 1, "Not recorded": 1}
    opened = await client.get("/api/v1/reports/group", params=[
        ("entity", "incident"), ("dimension", "form_kind"),
        ("filter", f"location:{area}"), ("filter", "category:first_aid")])
    assert sum(r["count"] for r in opened.json()) == 2
