"""★ Round 12, ACT-04: "allow the HSE role to create a CAPA on the feedback.
allow this capa to be viewable by the user who submitted the feedback."""
import uuid
from datetime import timedelta

from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user

DUE = (plant_today() + timedelta(days=7)).isoformat()


async def test_hse_raises_one_and_whoever_left_the_comment_follows_it(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    author = await make_user(test_engine, role="requester")
    stranger = await make_user(test_engine, role="requester")
    owner = await make_user(test_engine, role="requester")
    async with authed_client(make_token(author.id, author.role), db_session) as me:
        left = (await me.post("/api/v1/feedback", json={"body": "The eyewash is blocked"})).json()

        made = await hse.post("/api/v1/actions", json={
            "source_type": "feedback", "source_id": left["id"], "title": "Clear the eyewash",
            "owner_id": str(owner.id), "due_date": DUE})
        assert made.status_code == 201, made.text
        action = made.json()
        assert action["source_ref"].startswith("Feedback of ")

        mine = (await me.get("/api/v1/feedback")).json()
        assert [a["action_no"] for a in mine[0]["actions"]] == [action["action_no"]]
        opened = await me.get(f"/api/v1/actions/{action['id']}")
        assert opened.status_code == 200, "★ the person who left it can follow it"
        assert opened.json()["follow_only"] is True
        assert (await me.post(f"/api/v1/actions/{action['id']}/updates",
                              json={"body": "Me too"})).status_code == 403, "follow, not work on"

    async with authed_client(make_token(stranger.id, stranger.role), db_session) as them:
        assert (await them.get(f"/api/v1/actions/{action['id']}")).status_code == 403, \
            "somebody else's comment is not theirs to follow"


async def test_only_hse_raises_one_and_only_on_a_comment(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    owner = await make_user(test_engine, role="requester")
    boss = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(owner.id, owner.role), db_session) as me:
        left = (await me.post("/api/v1/feedback", json={"body": "Ladder is cracked"})).json()
    body = {"source_type": "feedback", "source_id": left["id"], "title": "Bin the ladder",
            "owner_id": str(owner.id), "due_date": DUE}
    async with authed_client(make_token(boss.id, boss.role), db_session) as sup:
        assert (await sup.post("/api/v1/actions", json=body)).status_code == 403
    assert (await hse.post("/api/v1/actions", json={
        **body, "source_id": str(uuid.uuid4())})).status_code == 422
    assert (await hse.post("/api/v1/actions", json=body)).status_code == 201
