"""Recording a recommendation, seeing what will be sent, and sending it.

Two requests from the third acceptance round, answered together because they
are one workflow:

    "allow the recommendation and the staff to send it to in one entry process"
    "create a process to review the communication in draft mode before it is
     sent to allow for configuration before sending it"

The part worth testing hardest is not the happy path. It is that the preview
tells the truth about who will actually receive the mail: `mail.recipients`
drops anybody inactive or opted out of email — silently, which is correct for a
second channel and wrong for a screen whose whole purpose is to say what is
about to happen. A recommendation "sent" to somebody set to `none` is owed a
reply by somebody who was never told, and at this plant that setting is
deliberate for anyone sharing a mailbox.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.communication import Communication
from app.models.mirrors import User
from tests.conftest import authed_client, make_token, make_user


async def _a_meeting(client) -> dict:
    r = await client.post("/api/v1/jhsc/meetings", json={
        "held_on": date.today().isoformat(), "title": "Monthly meeting"})
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
async def manager_with_email(test_engine):
    return await make_user(test_engine, role="requester", full_name="Reachable Manager")


@pytest.fixture
async def manager_opted_out(test_engine, db_session):
    """Somebody who will not be reached by email, in the way that actually happens.

    ★ Not an empty address — `users.email` is NOT NULL in the mirror, checked
    against the database rather than assumed. The real case is an opt-out, and
    it is not an edge case here: plant workers share a mailbox and are set to
    `none` on purpose, so a recommendation put in front of a supervisor who is
    also set that way reaches nobody while the reply stays owed.
    """
    user = await make_user(test_engine, role="requester", full_name="Unreachable Manager")
    row = await db_session.get(User, user.id)
    row.notification_channel = "none"
    await db_session.commit()
    return user


async def test_the_preview_shows_the_message_and_writes_nothing(
        hse_manager, manager_with_email):
    _, client = hse_manager
    meeting = await _a_meeting(client)

    before = (await client.get(
        f"/api/v1/jhsc/recommendations?meeting_id={meeting['id']}")).json()

    r = await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": meeting["id"],
        "recommendation": "Fit a guard to the number two filler",
        "user_ids": [str(manager_with_email.id)],
    })
    assert r.status_code == 200, r.text
    preview = r.json()

    assert meeting["meeting_no"] in preview["subject"]
    assert "Fit a guard to the number two filler" in preview["body"]
    # The clock is in the message, because the clock is the point of it.
    assert "twenty-one days" in preview["body"]
    assert preview["seq_will_be"] == 1

    after = (await client.get(
        f"/api/v1/jhsc/recommendations?meeting_id={meeting['id']}")).json()
    assert len(after) == len(before), "the preview recorded something"


async def test_the_preview_names_whoever_will_not_receive_it(
        hse_manager, manager_with_email, manager_opted_out):
    """★ And says who *will*, in the same assertion.

    A test that only checks the unreachable one passes just as well when
    everybody is unreachable, which is the failure this is guarding against.
    """
    _, client = hse_manager
    meeting = await _a_meeting(client)

    r = await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": meeting["id"],
        "recommendation": "Replace the ladder in 105",
        "user_ids": [str(manager_with_email.id), str(manager_opted_out.id)],
    })
    assert r.status_code == 200, r.text
    preview = r.json()

    by_id = {row["id"]: row for row in preview["recipients"]}
    assert by_id[str(manager_with_email.id)]["will_receive"] is True
    assert by_id[str(manager_opted_out.id)]["will_receive"] is False
    assert "opted out" in (by_id[str(manager_opted_out.id)]["reason"] or "")

    assert preview["unreachable"] == ["Unreachable Manager"]


async def test_recording_and_sending_happen_in_one_request(
        hse_manager, manager_with_email):
    _, client = hse_manager
    meeting = await _a_meeting(client)

    r = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
        "recommendation": "Re-train the packing crew on lockout",
        "send_to": [str(manager_with_email.id)],
    })
    assert r.status_code in (200, 201), r.text
    rec = r.json()

    assert rec["sent_at"] is not None, "it was recorded but not sent"
    assert [p["name"] for p in rec["sent_to"]] == ["Reachable Manager"]
    assert rec["sent_by_name"]


async def test_recording_without_naming_anybody_still_records(hse_manager):
    """The twenty-one days run from the recording, so this has to stay possible.

    A committee that has not decided who answers must still be able to start
    the clock — refusing to record until a recipient is named would let an
    unnamed recommendation sit outside the statute altogether.
    """
    _, client = hse_manager
    meeting = await _a_meeting(client)

    r = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
        "recommendation": "Review the confined space procedure",
    })
    assert r.status_code in (200, 201), r.text
    assert r.json()["sent_at"] is None
    assert r.json()["sent_to"] == []


async def test_the_edited_wording_is_what_gets_sent(
        hse_manager, manager_with_email, db_session):
    """What the committee approved in the preview, not what was composed for them."""
    _, client = hse_manager
    meeting = await _a_meeting(client)
    approved = "The committee's own wording, agreed at the meeting."

    r = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
        "recommendation": "Guard the conveyor nip point",
        "send_to": [str(manager_with_email.id)],
        "message": approved,
    })
    assert r.status_code in (200, 201), r.text

    mail = (await db_session.execute(
        select(Communication)
        .where(Communication.doc_id == uuid.UUID(meeting["id"]),
               Communication.direction == "out",
               # ★ Recording also queues the role-based notice, and both share a
               # created_at inside one transaction — ordering by time picks
               # whichever row the database returns first.
               Communication.template_key == "jhsc_recommendation_sent"))).scalars().first()
    assert mail is not None, "nothing was queued"
    assert mail.body_text is not None
    assert approved in mail.body_text
    # The standard sentence it replaced is gone, so this is an override and not
    # an append that happens to contain the right words.
    assert "twenty-one days" not in mail.body_text


async def test_the_preview_numbers_the_recommendation_as_it_will_be_numbered(
        hse_manager, manager_with_email):
    """A draft that says #1 and arrives as #2 is a draft nobody trusts again."""
    _, client = hse_manager
    meeting = await _a_meeting(client)

    first = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                              json={"recommendation": "The first thing"})
    assert first.status_code in (200, 201), first.text

    preview = (await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": meeting["id"], "recommendation": "The second thing",
        "user_ids": [str(manager_with_email.id)],
    })).json()
    assert preview["seq_will_be"] == 2
    assert "#2" in preview["subject"]

    recorded = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                                 json={"recommendation": "The second thing"})
    assert recorded.json()["seq"] == preview["seq_will_be"]


async def test_previewing_an_existing_recommendation_uses_its_own_number(
        hse_manager, manager_with_email):
    """★ Re-sending #1 previewed as #3 and sent as #1 — the send was right and the
    preview was not, which is the one thing this screen exists to prevent. Found
    in review by uniops-9a."""
    _, client = hse_manager
    meeting = await _a_meeting(client)

    first = (await client.post(
        f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
        json={"recommendation": "The first thing"})).json()
    await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                      json={"recommendation": "The second thing"})

    # Without the id: the next one to be recorded, which is #3.
    fresh = (await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": meeting["id"], "recommendation": "A third thing",
        "user_ids": [str(manager_with_email.id)]})).json()
    assert fresh["seq_will_be"] == 3

    # ★ With it: the one being re-sent.
    again = (await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": meeting["id"], "recommendation": first["recommendation"],
        "recommendation_id": first["id"],
        "user_ids": [str(manager_with_email.id)]})).json()
    assert again["seq_will_be"] == 1, again
    assert "#1" in again["subject"], again["subject"]


async def test_a_recommendation_from_another_meeting_is_refused(hse_manager):
    """One meeting's heading over another meeting's recommendation."""
    _, client = hse_manager
    a = await _a_meeting(client)
    b = await _a_meeting(client)
    rec = (await client.post(f"/api/v1/jhsc/meetings/{a['id']}/recommendations",
                             json={"recommendation": "Belongs to A"})).json()

    refused = await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": b["id"], "recommendation": "Belongs to A",
        "recommendation_id": rec["id"]})
    assert refused.status_code == 409, refused.text
    assert "different meeting" in refused.json()["detail"]


async def test_a_worker_cannot_preview_a_committee_notice(worker):
    """It composes what the committee is about to send, so it is theirs."""
    _, client = worker
    refused = await client.post("/api/v1/jhsc/recommendations/preview", json={
        "meeting_id": str(uuid.uuid4()), "recommendation": "Anything",
    })
    assert refused.status_code == 403, refused.text


class TestTheReplyReachesThePersonWhoOwesIt:
    """★ "the recommendations assigned to a supervisor do not show up in their
    my actions register."

    Sending emailed them and recorded that it had. This module's rule is that
    whoever must *do* something holds a task and email is the notice, not the
    assignment — and a written reply inside twenty-one days is as much an
    obligation as anything in the module.
    """

    async def test_sending_puts_the_reply_in_the_recipient_s_own_list(
            self, hse_manager, manager_with_email, db_session):
        _, client = hse_manager
        meeting = await _a_meeting(client)

        r = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
            "recommendation": "Guard the sealer entrance",
            "send_to": [str(manager_with_email.id)],
        })
        assert r.status_code in (200, 201), r.text

        async with authed_client(
                make_token(manager_with_email.id, manager_with_email.role),
                db_session) as theirs:
            mine = await theirs.get("/api/v1/actions/mine/tasks")
            assert mine.status_code == 200, mine.text
            titles = [t["title"] for t in mine.json()]
            assert any("recommendation" in t.lower() for t in titles), \
                f"the reply is not in their list: {titles}"

    async def test_redirecting_it_moves_the_obligation(
            self, hse_manager, manager_with_email, db_session, test_engine):
        """Re-sending replaces the recipients, so it must replace their tasks.

        A recommendation redirected to the right manager must stop being the
        wrong manager's to answer — otherwise the first person keeps a statutory
        task for something that is no longer theirs.
        """
        _, client = hse_manager
        wrong = await make_user(test_engine, role="requester", full_name="Wrong Manager")
        meeting = await _a_meeting(client)

        created = await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
                "recommendation": "Replace the eyewash signage",
                "send_to": [str(wrong.id)],
            })
        assert created.status_code in (200, 201), created.text
        rec_id = created.json()["id"]

        resent = await client.post(f"/api/v1/jhsc/recommendations/{rec_id}/send",
                                   json={"user_ids": [str(manager_with_email.id)]})
        assert resent.status_code == 200, resent.text

        async with authed_client(make_token(wrong.id, wrong.role), db_session) as theirs:
            left = [t["title"] for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()]
            assert not any("recommendation" in t.lower() for t in left), \
                f"the wrong manager still owes the reply: {left}"

        # ★ The admission: it moved rather than vanishing.
        async with authed_client(
                make_token(manager_with_email.id, manager_with_email.role),
                db_session) as theirs:
            now = [t["title"] for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()]
            assert any("recommendation" in t.lower() for t in now), \
                f"nobody owes the reply now: {now}"

    async def test_recording_without_sending_leaves_it_with_hse(self, hse_manager):
        """Unchanged, and deliberate: until somebody is named, the obligation is
        HSE's to place. The role-assigned task is what makes it visible to them."""
        _, client = hse_manager
        meeting = await _a_meeting(client)
        r = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations",
                              json={"recommendation": "Review the ladder procedure"})
        assert r.status_code in (200, 201), r.text

        mine = await client.get("/api/v1/actions/mine/tasks")
        assert mine.status_code == 200, mine.text
        assert any("recommendation" in t["title"].lower() for t in mine.json())


class TestOneMeetingTwoRecommendations:
    """★ The fault uniops-9a found in review, and the reason the task moved.

    The reply task was raised with `doc_id=meeting.id` — one document for every
    recommendation made at that meeting — so closing "the reply task" could only
    close all of them. Sending the second recommendation closed the first one's
    task while its twenty-one days were still running and nobody had answered
    it: the obligation stayed and the reminder went.

    The statutory deadline was never touched by this, which is what made it
    quiet. The clock kept running against a recommendation that had dropped off
    everybody's list.
    """

    async def test_sending_the_second_does_not_close_the_first_one_s_task(
            self, hse_manager, manager_with_email, test_engine, db_session):
        from tests.conftest import authed_client, make_token, make_user

        _, client = hse_manager
        other = await make_user(test_engine, role="requester", full_name="Second Manager")
        meeting = await _a_meeting(client)

        first = await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
                "recommendation": "Guard the sealer entrance",
                "send_to": [str(manager_with_email.id)]})
        assert first.status_code in (200, 201), first.text

        second = await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
                "recommendation": "Replace the eyewash signage",
                "send_to": [str(other.id)]})
        assert second.status_code in (200, 201), second.text

        # ★ The first recipient still owes their reply.
        async with authed_client(
                make_token(manager_with_email.id, manager_with_email.role),
                db_session) as theirs:
            mine = [t for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()
                    if "recommendation" in t["title"].lower()]
            assert mine, "sending the second recommendation closed the first one's task"
            assert "#1" in mine[0]["title"], mine[0]["title"]

        # And so does the second.
        async with authed_client(make_token(other.id, other.role), db_session) as theirs:
            mine = [t for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()
                    if "recommendation" in t["title"].lower()]
            assert mine and "#2" in mine[0]["title"], mine

    async def test_answering_one_leaves_the_other_owed(
            self, hse_manager, manager_with_email, test_engine, db_session):
        """★ The other half. `respond()` used to wait until nothing on the whole
        meeting was outstanding — a detour that existed only because the tasks
        shared a document. Now answering one answers one, and the test proves it
        does not answer both."""
        from tests.conftest import authed_client, make_token, make_user

        _, client = hse_manager
        other = await make_user(test_engine, role="requester", full_name="Third Manager")
        meeting = await _a_meeting(client)

        first = (await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
                "recommendation": "Fit a guard", "send_to": [str(manager_with_email.id)]})).json()
        second = (await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
                "recommendation": "Re-brief the crew", "send_to": [str(other.id)]})).json()

        answered = await client.post(f"/api/v1/jhsc/recommendations/{first['id']}/respond",
                                     json={"response": "Guard fitted", "outcome": "accepted"})
        assert answered.status_code == 200, answered.text

        # The one that was answered is off their list.
        async with authed_client(
                make_token(manager_with_email.id, manager_with_email.role),
                db_session) as theirs:
            left = [t for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()
                    if "recommendation" in t["title"].lower()]
            assert not left, f"answering it did not clear the task: {left}"

        # ★ The one that was not is still owed — this is what used to break the
        # other way round.
        async with authed_client(make_token(other.id, other.role), db_session) as theirs:
            still = [t for t in (await theirs.get("/api/v1/actions/mine/tasks")).json()
                     if "recommendation" in t["title"].lower()]
            assert still, "answering one recommendation cleared another one's task"
            assert "#2" in still[0]["title"], still[0]["title"]
        assert second["seq"] == 2
