"""★ Round 12, batch 5: WHO-01, TC-13 and D13.

Every corrective action given to somebody — raised, reassigned or handed over
from a leaver — emails its owner; Who is told adds the previous owner, the
person who reported it and the owner's supervisor. A handover is written on
each record it moves.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.models.change_log import ChangeLogEntry
from app.models.communication import Communication
from app.models.config import EhsConfig
from app.models.mirrors import User
from app.services.day_range import plant_today
from tests.conftest import make_user
from tests.test_incident_anonymity_and_edit import _report

DUE = (plant_today() + timedelta(days=7)).isoformat()


@pytest.fixture(autouse=True)
async def _config(db_session):
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.flush()


async def _rule(hse, **boxes):
    r = await hse.put("/api/v1/settings/config", json={"action_assigned_notify": {
        "people": [], "previous_owner": True, "reporter": False, "supervisor": False, **boxes}})
    assert r.status_code == 200, r.text


async def _mail(db, template, doc_id) -> list[Communication]:
    return list((await db.execute(select(Communication).where(
        Communication.template_key == template,
        Communication.doc_id == uuid.UUID(str(doc_id))))).scalars())


async def test_an_action_on_anything_tells_its_owner(test_engine, db_session, hse_manager):
    """★ Only an incident's actions were emailed; a hazard's or an inspection's
    owner had a task and no word."""
    _, hse = hse_manager
    owner = await make_user(test_engine, role="requester")
    made = (await hse.post("/api/v1/actions", json={
        "source_type": "inspection", "source_id": str(uuid.uuid4()), "title": "Fix the guard",
        "owner_id": str(owner.id), "due_date": DUE})).json()
    [m] = await _mail(db_session, "action_owner_told", made["id"])
    assert m.to_addrs == [owner.email] and made["action_no"] in m.body_text
    log = (await hse.get("/api/v1/communications",
                         params={"doc_type": "ehs_act", "doc_id": made["id"]})).json()
    assert [x["id"] for x in log] == [str(m.id)], "listed on the action"


async def test_the_boxes_add_the_raiser_and_the_supervisor(test_engine, db_session, hse_manager):
    hse_user, hse = hse_manager
    boss = await make_user(test_engine, role="area_supervisor")
    owner = await make_user(test_engine, role="requester")
    await db_session.execute(update(User).where(User.id == owner.id).values(supervisor_id=boss.id))
    await db_session.flush()

    made = (await hse.post("/api/v1/actions", json={
        "source_type": "manual", "title": "Before the boxes",
        "owner_id": str(owner.id), "due_date": DUE})).json()
    [m] = await _mail(db_session, "action_owner_told", made["id"])
    assert m.to_addrs == [owner.email], "the boxes start unticked"

    await _rule(hse, reporter=True, supervisor=True)
    made = (await hse.post("/api/v1/actions", json={
        "source_type": "manual", "title": "After the boxes",
        "owner_id": str(owner.id), "due_date": DUE})).json()
    [m] = await _mail(db_session, "action_owner_told", made["id"])
    assert set(m.to_addrs) == {owner.email, boss.email, hse_user.email}, \
        "D13: off an incident, the reporter is whoever raised it"


async def test_an_anonymous_reporter_is_never_added(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    await _rule(hse, reporter=True)
    reporter, iid = await _report(test_engine, db_session, anonymous=True, kind="near_miss")
    owner = await make_user(test_engine, role="requester")
    assert (await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid, "title": "Mop the floor",
        "hierarchy_of_control": "administrative", "owner_id": str(owner.id),
        "due_date": DUE})).status_code == 201
    [m] = await _mail(db_session, "action_owner_told", iid)
    assert m.to_addrs == [owner.email]
    assert "Dana Reporter" not in m.body_text

    named, iid2 = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid2, "title": "Mop the floor",
        "hierarchy_of_control": "administrative", "owner_id": str(owner.id), "due_date": DUE})
    [m] = await _mail(db_session, "action_owner_told", iid2)
    assert set(m.to_addrs) == {owner.email, named.email}, "the admission: a named reporter is added"


async def test_a_reassignment_tells_the_new_owner_and_the_previous_one(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    first = await make_user(test_engine, role="requester", full_name="Fay First")
    second = await make_user(test_engine, role="requester", full_name="Sam Second")
    made = (await hse.post("/api/v1/actions", json={
        "source_type": "manual", "title": "Label the valves",
        "owner_id": str(first.id), "due_date": DUE})).json()
    assert (await hse.post(f"/api/v1/actions/{made['id']}/reassign", json={
        "owner_id": str(second.id), "reason": "Fay is on nights"})).status_code == 200
    [m] = await _mail(db_session, "action_reassigned", made["id"])
    assert set(m.to_addrs) == {first.email, second.email}
    assert "Fay First" in m.body_text and "Fay is on nights" in m.body_text

    await _rule(hse, previous_owner=False)
    assert (await hse.post(f"/api/v1/actions/{made['id']}/reassign", json={
        "owner_id": str(first.id), "reason": "Back on days"})).status_code == 200
    latest = [x for x in await _mail(db_session, "action_reassigned", made["id"])
              if "Back on days" in x.body_text]
    assert [x.to_addrs for x in latest] == [[first.email]], "the previous owner box is HSE's"


async def test_a_handover_is_written_on_each_record(test_engine, db_session, hse_manager):
    """★ TC-13: kept only on the handover, so a hazard opened later did not say
    how or why it changed hands."""
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="area_supervisor", full_name="Lee Leaver")
    heir = await make_user(test_engine, role="area_supervisor", full_name="Hal Heir")
    hazard = (await hse.post("/api/v1/hazards", json={
        "title": "Unguarded nip", "owner_id": str(leaver.id)})).json()
    assert hazard.get("id"), hazard
    done = await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"hazard": str(heir.id)}, "reason": "Moved to the other plant"})
    assert done.status_code == 200, done.text
    await db_session.flush()
    trail = list((await db_session.execute(select(ChangeLogEntry).where(
        ChangeLogEntry.table_name == "ehs_hazards", ChangeLogEntry.row_id == hazard["id"],
        ChangeLogEntry.field == "handed over"))).scalars())
    assert [(e.old_value, e.new_value) for e in trail] == [
        ("Lee Leaver", "Hal Heir — Moved to the other plant")]


async def test_the_rule_refuses_a_box_it_does_not_have(hse_manager):
    _, hse = hse_manager
    r = await hse.put("/api/v1/settings/config", json={"action_assigned_notify": {
        "people": [], "manager": True}})
    assert r.status_code == 422
