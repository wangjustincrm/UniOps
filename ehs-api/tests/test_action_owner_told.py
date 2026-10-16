"""The owner of a corrective action raised on an incident is told, and the
message is listed with the incident.

Round 7, TC-09: "when a root cause is added and/or a capa is added to an
incident, log the communication to the capa task owner in the notification
sent log on the incident main form."
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.models.communication import Communication
from tests.conftest import make_user
from tests.test_incident_anonymity_and_edit import _report


async def _told(db, incident_id: str) -> list[Communication]:
    return list((await db.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(incident_id),
        Communication.template_key == "action_owner_told"))).scalars())


def _action(source_id, owner, **kw):
    return {"source_type": "incident", "source_id": source_id, "title": "Fit a drain",
            "hierarchy_of_control": "engineering", "owner_id": str(owner.id),
            "due_date": (date.today() + timedelta(days=7)).isoformat(), **kw}


async def test_the_owner_is_told_and_it_is_on_the_incident(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    tree = (await hse.put(f"/api/v1/incidents/{iid}/causes", json={"causes": [
        {"cause_type": "root", "label": "No drain under the dryer"}]})).json()
    owner = await make_user(test_engine, role="area_supervisor", full_name="Olive Owner")
    made = await hse.post("/api/v1/actions",
                          json=_action(iid, owner, cause_id=tree["root"][0]["id"]))
    assert made.status_code == 201, made.text

    told = await _told(db_session, iid)
    assert len(told) == 1 and told[0].to_addrs == [owner.email]
    assert "No drain under the dryer" in told[0].body_text and made.json()["action_no"] in told[0].subject
    log = (await hse.get("/api/v1/communications",
                         params={"doc_type": "ehs_inc", "doc_id": iid})).json()
    assert any(m["subject"] == told[0].subject for m in log), "listed under Notifications sent"


async def test_an_anonymous_reporter_is_not_named_to_the_owner(test_engine, db_session, hse_manager):
    """★ uniops-55: the message must not lead the owner back to the reporter."""
    _, hse = hse_manager
    reporter, iid = await _report(test_engine, db_session, anonymous=True, kind="near_miss")
    owner = await make_user(test_engine, role="area_supervisor")
    assert (await hse.post("/api/v1/actions", json=_action(iid, owner))).status_code == 201
    told = await _told(db_session, iid)
    assert told and all("Dana Reporter" not in (m.body_text + m.subject) for m in told)
    assert all(reporter.email not in m.to_addrs for m in told)


async def test_an_action_on_something_else_files_nothing_on_an_incident(
        test_engine, db_session, hse_manager):
    """The admission beside it: not every action hangs on an incident."""
    _, hse = hse_manager
    owner = await make_user(test_engine, role="area_supervisor")
    other = uuid.uuid4()
    assert (await hse.post("/api/v1/actions", json=_action(
        str(other), owner, source_type="inspection"))).status_code == 201
    assert await _told(db_session, str(other)) == []
