"""UAT 13: a supervisor runs a past toolbox talk again (R13-C)."""
import uuid

from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user


async def _published_talk(hse, sup):
    talk = (await hse.post("/api/v1/toolbox-talks", json={
        "title": f"Ladders {uuid.uuid4().hex[:4]}",
        "period_month": plant_today().replace(day=1).isoformat()})).json()
    sent = await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish",
                          json={"supervisor_ids": [str(sup.id)]})
    assert sent.status_code == 200, sent.text
    return talk, sent.json()[0]


async def test_a_supervisor_holds_a_past_talk_again_with_a_new_sheet(
        hse_manager, worker, test_engine, db_session):
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor", full_name="Ken Again")
    talk, first = await _published_talk(hse, sup)
    async with authed_client(make_token(sup.id, sup.role), db_session) as k:
        signed = await k.post(f"/api/v1/toolbox-talks/deliveries/{first['id']}/sign", json={
            "signatures": [{"person_name": "Dario", "signature": "data:image/png;base64,AA"}],
            "held_at": "2026-11-02T08:00:00Z"})
        assert signed.status_code == 200, signed.text

        again = await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again")
        assert again.status_code == 201, again.text
        rerun = again.json()
        assert rerun["rerun_of"] == first["id"] and rerun["status"] == "pending"
        assert rerun["signed_count"] == 0   # a new sheet
        done = await k.post(f"/api/v1/toolbox-talks/deliveries/{rerun['id']}/sign", json={
            "signatures": [{"person_name": "Priya", "signature": "data:image/png;base64,AA"}],
            "held_at": "2026-11-16T08:00:00Z"})
        assert done.status_code == 200 and done.json()["status"] == "delivered"
        # And again — as often as it is needed.
        assert (await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again")).status_code == 201

    # Recorded for tracking: both deliveries are in the month's tracker.
    page = (await hse.get("/api/v1/toolbox-talks/tracker",
                          params={"month": plant_today().isoformat(), "q": "Ken Again"})).json()
    assert page["items"][0]["delivered"] == 2
    # Publishing again still does not double the first delivery.
    await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish", json={"supervisor_ids": [str(sup.id)]})
    firsts = [d for d in (await hse.get("/api/v1/toolbox-talks/deliveries",
                                        params={"talk_id": talk["id"]})).json() if not d["rerun_of"]]
    assert len(firsts) == 1


async def test_only_those_who_hold_talks_may_hold_one_again(hse_manager, worker, test_engine, db_session):
    _, hse = hse_manager
    _, emp = worker
    sup = await make_user(test_engine, role="area_supervisor")
    talk, _ = await _published_talk(hse, sup)
    assert (await emp.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again")).status_code == 403
    # HSE may, and so may a lead hand a talk was handed to before.
    assert (await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again")).status_code == 201
    lead = await make_user(test_engine, full_name="Lead Hand")
    await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish", json={"supervisor_ids": [str(lead.id)]})
    async with authed_client(make_token(lead.id, lead.role), db_session) as c:
        assert (await c.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again")).status_code == 201
    unpublished = (await hse.post("/api/v1/toolbox-talks", json={
        "title": "Draft", "period_month": plant_today().replace(day=1).isoformat()})).json()
    assert (await hse.post(f"/api/v1/toolbox-talks/{unpublished['id']}/hold-again")).status_code == 409
