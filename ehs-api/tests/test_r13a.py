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
