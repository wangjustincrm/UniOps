"""What an incident waits for before it closes — and the way through each.

Round 7, answer box: "when a return to work plan is created in an incident, the
incident can not be closed until the return to work form indicates they are
back to full duties" and "on the medical incident form, if a mol or wsib clock
is started but not ended, the incident should not be allowed to close or the
HSE review signed off as completed."
"""
from datetime import date, datetime, timedelta, timezone

from tests.conftest import authed_client, make_token, make_user
from app.services.day_range import plant_today

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


async def _unconscious(test_engine, db):
    """Reported as Unconscious: the MOL 48-hour clock starts on submission."""
    reporter = await make_user(test_engine, role="requester")
    async with authed_client(make_token(reporter.id, reporter.role), db) as c:
        made = await c.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Collapsed by the boiler",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "incident_type": "unconscious", "injury_class": "medical_aid",
            "signature": SIGNATURE, "description": "Found on the floor."})
        assert made.status_code in (200, 201), made.text
        iid = made.json()["id"]
        assert (await c.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
    return iid


async def test_a_running_clock_holds_the_review_and_the_close(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    iid = await _unconscious(test_engine, db_session)
    review = await hse.post(f"/api/v1/incidents/{iid}/hse-review")
    assert review.status_code == 409 and "MOL 48-hour report" in review.text and "due" in review.text
    close = await hse.post(f"/api/v1/incidents/{iid}/close")
    assert close.status_code == 409

    # The way through: each clock filed, or said not to apply.
    clocks = (await hse.get(f"/api/v1/incidents/{iid}")).json()["deadlines"]
    for d in clocks:
        got = await hse.post(f"/api/v1/deadlines/{d['id']}/not-required",
                             json={"reason": "Fainted from heat; no injury, MOL confirmed not reportable"})
        assert got.status_code == 200, got.text
    after = (await hse.get(f"/api/v1/incidents/{iid}")).json()["deadlines"]
    assert all(d["evidence_note"].startswith("Not required:") for d in after)
    assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
    assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200


async def test_only_hse_says_a_clock_does_not_apply(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    iid = await _unconscious(test_engine, db_session)
    clock = (await hse.get(f"/api/v1/incidents/{iid}")).json()["deadlines"][0]
    sup = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(sup.id, sup.role), db_session) as c:
        refused = await c.post(f"/api/v1/deadlines/{clock['id']}/not-required",
                               json={"reason": "Does not apply here"})
    assert refused.status_code == 403
    assert (await hse.post(f"/api/v1/deadlines/{clock['id']}/not-required",
                           json={"reason": "x"})).status_code == 422, "a reason is required"


async def test_an_active_return_to_work_plan_holds_the_close(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    worker = await make_user(test_engine, role="requester", full_name="Dana Worker")
    async with authed_client(make_token(worker.id, worker.role), db_session) as c:
        made = await c.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Strained back lifting",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
            "incident_type": "first_aid", "signature": SIGNATURE, "description": "Lifting.",
            "persons": [{"role": "injured", "user_id": str(worker.id), "person_name": "Dana Worker"}]})
        iid = made.json()["id"]
        assert (await c.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
    plan = await hse.post(f"/api/v1/incidents/{iid}/return-to-work", json={
        "modified_duties": "Light packing only",
        "start_date": plant_today().isoformat()})
    assert plan.status_code in (200, 201), plan.text
    assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200

    held = await hse.post(f"/api/v1/incidents/{iid}/close")
    assert held.status_code == 409 and "Back on full duties" in held.text

    back = await hse.post(f"/api/v1/incidents/{iid}/return-to-work/{plan.json()['id']}/close", json={})
    assert back.status_code == 200, back.text
    assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
