"""The figures above the incident register.

Round 7, TC-10: "create a dashboard on the incident registry with the following
boxes, Open Incidents, number of closed incidents, Incidents under
investigation, number of Root causes identified, number of CAPA's recorded."
"""
from datetime import date, datetime, timedelta, timezone

from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_anonymity_and_edit import _report

CAUSES = {"causes": [
    {"cause_type": "immediate", "label": "Wet floor"},
    {"cause_type": "root", "label": "No drain under the dryer"},
    {"cause_type": "root", "label": "Mopping not on the shift checklist"},
]}


async def test_the_figures_count_what_the_register_shows(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    before = (await hse.get("/api/v1/incidents/summary")).json()

    _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    assert (await hse.put(f"/api/v1/incidents/{iid}/causes", json=CAUSES)).status_code == 200
    owner = await make_user(test_engine, role="area_supervisor")
    action = {"source_type": "incident", "source_id": iid, "title": "Fit a drain",
              "hierarchy_of_control": "engineering", "owner_id": str(owner.id),
              "due_date": (date.today() + timedelta(days=7)).isoformat()}
    kept = await hse.post("/api/v1/actions", json=action)
    dropped = await hse.post("/api/v1/actions", json={**action, "title": "Mop more"})
    assert kept.status_code == 201 and dropped.status_code == 201
    from app.models.action import Action
    import uuid as _uuid
    row = await db_session.get(Action, _uuid.UUID(dropped.json()["id"]))
    row.status = "cancelled"
    await db_session.commit()

    # ★ A draft is not an incident yet.
    reporter = await make_user(test_engine, role="requester")
    async with authed_client(make_token(reporter.id, reporter.role), db_session) as c:
        assert (await c.post("/api/v1/incidents", json={
            "form_kind": "near_miss", "title": "Not sent yet", "incident_type": "near_miss",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "description": "Draft."})).status_code in (200, 201)

    after = (await hse.get("/api/v1/incidents/summary")).json()
    assert after["open"] == before["open"] + 1
    assert after["root_causes"] == before["root_causes"] + 2
    assert after["corrective_actions"] == before["corrective_actions"] + 1, "cancelled is not recorded"


async def test_without_the_register_only_your_own(test_engine, db_session, hse_manager):
    """The same narrowing as the list: an employee's figures are their reports."""
    reporter, _ = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    await _report(test_engine, db_session, anonymous=False, kind="near_miss")  # somebody else's
    async with authed_client(make_token(reporter.id, reporter.role), db_session) as c:
        mine = (await c.get("/api/v1/incidents/summary")).json()
    assert mine["open"] == 1
    _, hse = hse_manager
    assert (await hse.get("/api/v1/incidents/summary")).json()["open"] >= 2


async def test_each_figure_opens_the_incidents_behind_it(test_engine, db_session, hse_manager):
    """★ Round 8, TC-08: "make the number in the boxes clickable so that the
    user … is shown the incidents that make up that number." """
    _, hse = hse_manager
    _, with_both = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    _, plain = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    assert (await hse.put(f"/api/v1/incidents/{with_both}/causes", json=CAUSES)).status_code == 200
    owner = await make_user(test_engine, role="area_supervisor")
    assert (await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": with_both, "title": "Fit a drain",
        "hierarchy_of_control": "engineering", "owner_id": str(owner.id),
        "due_date": (date.today() + timedelta(days=7)).isoformat()})).status_code == 201

    for flag in ("has_root_causes", "has_actions"):
        ids = {i["id"] for i in (await hse.get(
            "/api/v1/incidents", params={flag: "true", "limit": 200})).json()}
        assert with_both in ids and plain not in ids, flag


async def test_corrective_actions_raised_completed_and_outstanding(test_engine, db_session, hse_manager):
    """★ Round 10, TC-10: "a capa counter tracking all the capa created,
    completed and outstanding" on the incident register, each opening the
    incidents behind it."""
    import uuid as _uuid
    from app.models.action import Action

    _, hse = hse_manager
    before = (await hse.get("/api/v1/incidents/summary")).json()
    _, done = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    _, waiting = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    owner = await make_user(test_engine, role="area_supervisor")
    made = {}
    for iid in (done, waiting):
        r = await hse.post("/api/v1/actions", json={
            "source_type": "incident", "source_id": iid, "title": "Fix it",
            "owner_id": str(owner.id), "due_date": (date.today() + timedelta(days=7)).isoformat()})
        assert r.status_code == 201, r.text
        made[iid] = r.json()["id"]
    row = await db_session.get(Action, _uuid.UUID(made[done]))
    row.status = "closed"
    await db_session.commit()

    after = (await hse.get("/api/v1/incidents/summary")).json()
    assert after["corrective_actions"] == before["corrective_actions"] + 2
    assert after["actions_completed"] == before["actions_completed"] + 1
    assert after["actions_outstanding"] == before["actions_outstanding"] + 1

    def ids(state):
        return hse.get("/api/v1/incidents", params={"actions_state": state, "limit": 200})
    completed = {i["id"] for i in (await ids("completed")).json()}
    outstanding = {i["id"] for i in (await ids("outstanding")).json()}
    assert done in completed and waiting not in completed
    assert waiting in outstanding and done not in outstanding
