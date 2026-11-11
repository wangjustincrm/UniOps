"""★ Round 12, batch 5: the Communications log.

COM-01 every email, not only the undelivered; COM-03 searched on the person,
the record's number and dates, with a reminder sent again and filed on the
record; Q16 HSE only, reminders to people it went to, and the words removed
after the retention period while who was told stays.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models.communication import Communication
from app.models.config import EhsConfig
from app.services import mail
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_anonymity_and_edit import _report


@pytest.fixture(autouse=True)
async def _config(db_session):
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.flush()


async def _mail_about_an_incident(test_engine, db_session, hse, name="Wanda Worker"):
    owner = await make_user(test_engine, role="requester", full_name=name)
    _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    made = await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid, "title": "Mop up",
        "hierarchy_of_control": "administrative", "owner_id": str(owner.id),
        "due_date": (plant_today() + timedelta(days=5)).isoformat()})
    assert made.status_code == 201, made.text
    row = (await db_session.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(iid),
        Communication.template_key == "action_owner_told"))).scalar_one()
    return owner, iid, row


async def test_every_email_is_listed_and_searched_without_its_words(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    owner, iid, row = await _mail_about_an_incident(test_engine, db_session, hse)
    await db_session.execute(update(Communication).where(Communication.id == row.id)
                             .values(sent_at=datetime.now(timezone.utc)))
    await db_session.flush()

    page = (await hse.get("/api/v1/communications/log", params={"q": "Wanda Work"})).json()
    assert [r["id"] for r in page["items"]] == [str(row.id)], "★ found by the person's name"
    got = page["items"][0]
    assert got["to_names"] == ["Wanda Worker"] and got["path"] == f"/incidents/{iid}"
    assert "body_text" not in got, "the list carries no words"
    assert got["doc_number"] and got["doc_number"].startswith("INC")
    assert page["retention_days"] == 21

    by_number = (await hse.get("/api/v1/communications/log", params={"q": got["doc_number"]})).json()
    assert str(row.id) in [r["id"] for r in by_number["items"]]
    assert str(row.id) in [r["id"] for r in (await hse.get(
        "/api/v1/communications/log", params={"state": "sent", "q": "Wanda"})).json()["items"]], \
        "★ COM-01: a sent one is listed, not only the undelivered"
    later = (plant_today() + timedelta(days=1)).isoformat()
    assert (await hse.get("/api/v1/communications/log",
                          params={"q": "Wanda", "from": later})).json()["total"] == 0
    assert (await hse.get("/api/v1/communications/log",
                          params={"q": "Wanda", "to": plant_today().isoformat()})).json()["total"] == 1
    assert (await hse.get("/api/v1/communications/log", params={"q": "100%_"})).json()["total"] == 0
    assert (await hse.get("/api/v1/communications/log", params={"sort": "body"})).status_code == 422

    words = (await hse.get(f"/api/v1/communications/log/{row.id}")).json()
    assert "Mop up" in words["body_text"], "the words, one message at a time"


async def test_only_hse_sees_it(test_engine, db_session, hse_manager):
    """Q16: "Should the coordinator see this page too?" — no."""
    _, hse = hse_manager
    _, _, row = await _mail_about_an_incident(test_engine, db_session, hse)
    for role in ("ehs_coordinator", "area_supervisor", "requester"):
        who = await make_user(test_engine, role=role)
        async with authed_client(make_token(who.id, who.role), db_session) as c:
            assert (await c.get("/api/v1/communications/log")).status_code == 403, role
            assert (await c.get(f"/api/v1/communications/log/{row.id}")).status_code == 403, role
            assert (await c.post(f"/api/v1/communications/{row.id}/remind",
                                 json={"to": row.to_addrs})).status_code == 403, role


async def test_a_reminder_is_a_new_message_on_the_record_to_people_it_went_to(
        test_engine, db_session, hse_manager):
    hse_user, hse = hse_manager
    owner, iid, row = await _mail_about_an_incident(test_engine, db_session, hse)
    refused = await hse.post(f"/api/v1/communications/{row.id}/remind",
                             json={"to": [owner.email, "somebody@elsewhere.example"]})
    assert refused.status_code == 422, "★ not a way to send it to anybody else"
    assert (await hse.post(f"/api/v1/communications/{row.id}/remind",
                           json={"to": []})).status_code == 422

    sent = await hse.post(f"/api/v1/communications/{row.id}/remind", json={"to": [owner.email.upper()]})
    assert sent.status_code == 201, sent.text
    again = sent.json()
    assert again["resend_of"] == str(row.id) and again["subject"].startswith("Reminder: ")
    assert again["to_addrs"] == [owner.email]
    assert again["resent_by_name"] == hse_user.full_name
    first = await db_session.get(Communication, row.id)
    await db_session.refresh(first)
    assert not first.subject.startswith("Reminder"), "what was sent stays what was sent"
    on_record = (await hse.get("/api/v1/communications",
                               params={"doc_type": "ehs_inc", "doc_id": iid})).json()
    assert str(again["id"]) in [m["id"] for m in on_record], "★ logged on the record"
    assert (await hse.get("/api/v1/communications/log",
                          params={"state": "reminders", "q": "Wanda"})).json()["total"] == 1


async def test_reminder_text_is_escaped(test_engine, db_session, hse_manager):
    """★ What was typed into the first message is escaped again, not pasted
    into the reminder's HTML."""
    _, hse = hse_manager
    target = await make_user(test_engine, role="requester")
    row = await mail.queue(db_session, to=[target.email], subject="Note",
                           body='<a href="https://elsewhere/login">Sign in</a>',
                           template_key="t")
    again = await mail.remind(db_session, row, to=[target.email], by_name="HSE")
    assert "<a href=\"https://elsewhere" not in again.body_html
    assert "&lt;a href=" in again.body_html


async def test_the_words_go_after_the_retention_period_and_who_was_told_stays(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    owner, iid, row = await _mail_about_an_incident(test_engine, db_session, hse)
    now = datetime.now(timezone.utc)
    await db_session.execute(update(Communication).where(Communication.id == row.id)
                             .values(created_at=now - timedelta(days=22)))
    assert await mail.purge_bodies(db_session, now=now, days=0) == 0, "0 keeps them for good"
    assert await mail.purge_bodies(db_session, now=now, days=21) >= 1
    await db_session.flush()
    gone = await db_session.get(Communication, row.id)
    await db_session.refresh(gone)
    assert gone.body_text is None and gone.body_html is None and gone.body_purged_at
    assert gone.to_addrs == [owner.email] and gone.subject, "who, and about what, stays"
    assert (await hse.post(f"/api/v1/communications/{row.id}/remind",
                           json={"to": [owner.email]})).status_code == 409

    assert (await hse.put("/api/v1/settings/config",
                          json={"communication_retention_days": 30})).status_code == 200
    assert (await hse.get("/api/v1/communications/log")).json()["retention_days"] == 30
    assert (await hse.put("/api/v1/settings/config",
                          json={"communication_retention_days": -1})).status_code == 422
