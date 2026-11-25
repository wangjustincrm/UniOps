"""What a permit refuses, found while checking UAT 16's confined space request.

On the test server 9 of 17 issued permits were issued after their own end time
and 2 were live before they started; a confined space test with no H2S, CO or
LEL reading passed; and anybody who may request permits could log a gas test or
a signature on anybody's permit, a closed one included.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.permit import PermitType
from app.services import permit as rules
from tests.conftest import authed_client, make_token, make_user

ALL_FOUR = {"oxygen_pct": 20.9, "lel_pct": 0, "h2s_ppm": 0, "co_ppm": 0}
SIG = {"person_name": "Signer", "signature": "data:image/png;base64,iVBORw0KGgo="}


def _now():
    return datetime.now(timezone.utc)


async def _type(db, code, *, gas=True):
    row = PermitType(id=uuid.uuid4(), code=code, name=code.title(), precautions=[],
                     requires_gas_test=gas, required_signatures=["issuer"],
                     max_duration_hours=8)
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
async def confined(db_session):
    return await _type(db_session, "CONFINED_SPACE")


@pytest.fixture
async def simple(db_session):
    return await _type(db_session, "LOTO", gas=False)


async def _raise(client, kind, *, start=-1.0, end=4.0):
    got = await client.post("/api/v1/permits", json={
        "type_id": str(kind.id), "work_description": "Inspect the tank",
        "valid_from": (_now() + timedelta(hours=start)).isoformat(),
        "valid_to": (_now() + timedelta(hours=end)).isoformat()})
    assert got.status_code == 201, got.text
    return got.json()["id"]


async def _sign(client, pid):
    return await client.post(f"/api/v1/permits/{pid}/signatures", json={"capacity": "issuer", **SIG})


async def _issued(hse, pid):
    assert (await _sign(hse, pid)).status_code == 201
    got = await hse.post(f"/api/v1/permits/{pid}/issue", json={})
    assert got.status_code == 200, got.text
    return got.json()


async def test_a_permit_whose_time_has_passed_cannot_be_issued(hse_manager, simple):
    _, hse = hse_manager
    pid = await _raise(hse, simple, start=-5, end=-1)
    await _sign(hse, pid)
    refused = await hse.post(f"/api/v1/permits/{pid}/issue", json={})
    assert refused.status_code == 422 and "end time has passed" in refused.text
    # Given a new end, it may be.
    await hse.patch(f"/api/v1/permits/{pid}", json={"valid_to": (_now() + timedelta(hours=2)).isoformat()})
    assert (await hse.post(f"/api/v1/permits/{pid}/issue", json={})).json()["status"] == "issued"


async def test_a_permit_issued_for_later_is_not_live_yet(hse_manager, simple):
    _, hse = hse_manager
    pid = await _raise(hse, simple, start=2, end=5)
    assert (await _issued(hse, pid))["status"] == "scheduled"
    live = [p["id"] for p in (await hse.get("/api/v1/permits", params={"live": True})).json()]
    assert pid not in live
    # It is authorised all the same: not editable, not signed again, stoppable.
    assert (await hse.patch(f"/api/v1/permits/{pid}", json={"work_description": "x"})).status_code == 409
    assert (await _sign(hse, pid)).status_code == 409
    again = await hse.post(f"/api/v1/permits/{pid}/issue", json={})
    assert again.status_code == 200 and again.json()["status"] == "scheduled"
    stopped = await hse.post(f"/api/v1/permits/{pid}/suspend", json={"reason": "Called off"})
    assert stopped.json()["status"] == "suspended"


async def test_a_permit_that_has_started_is_live(hse_manager, simple):
    _, hse = hse_manager
    pid = await _raise(hse, simple)
    assert (await _issued(hse, pid))["status"] == "issued"
    live = [p["id"] for p in (await hse.get("/api/v1/permits", params={"live": True})).json()]
    assert pid in live


def test_the_status_at_each_end():
    start, end = _now(), _now() + timedelta(hours=4)
    at = lambda m: rules.effective_status("issued", end, start + timedelta(minutes=m), start)  # noqa: E731
    assert at(-1) == "scheduled" and at(0) == "issued" and at(1) == "issued" and at(241) == "expired"
    # Nothing but an issued permit waits to start.
    early = start - timedelta(hours=1)
    assert rules.effective_status("draft", end, early, start) == "draft"
    assert rules.effective_status("suspended", end, early, start) == "suspended"
    # A permit with no start behaves as before.
    assert rules.effective_status("issued", end, early) == "issued"


@pytest.mark.parametrize("missing", ["lel_pct", "h2s_ppm", "co_ppm", "oxygen_pct"])
async def test_a_confined_space_needs_all_four_gases(hse_manager, confined, missing):
    _, hse = hse_manager
    pid = await _raise(hse, confined)
    await _sign(hse, pid)
    got = await hse.post(f"/api/v1/permits/{pid}/gas",
                         json={k: v for k, v in ALL_FOUR.items() if k != missing})
    assert got.status_code == 201 and got.json()["is_acceptable"] is False
    assert len(got.json()["failed"]) == 1 and "not measured" in got.json()["failed"][0]
    assert (await hse.post(f"/api/v1/permits/{pid}/issue", json={})).status_code == 422

    assert (await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)).json()["is_acceptable"] is True
    assert (await hse.post(f"/api/v1/permits/{pid}/issue", json={})).status_code == 200


async def test_other_work_may_still_leave_a_gas_out(hse_manager, db_session):
    _, hse = hse_manager
    hot = await _type(db_session, "HOT_WORK")
    pid = await _raise(hse, hot)
    got = await hse.post(f"/api/v1/permits/{pid}/gas", json={"oxygen_pct": 20.9})
    assert got.json()["is_acceptable"] is True
    kinds = {t["code"]: t["requires_all_gases"] for t in (await hse.get("/api/v1/permit-types")).json()}
    assert kinds["HOT_WORK"] is False


async def test_a_test_passed_before_all_four_were_asked_for_does_not_issue(
        hse_manager, confined, db_session):
    from app.models.permit import PermitGasReading
    _, hse = hse_manager
    pid = await _raise(hse, confined)
    await _sign(hse, pid)
    db_session.add(PermitGasReading(
        id=uuid.uuid4(), permit_id=uuid.UUID(pid), taken_at=_now(),
        oxygen_pct=Decimal("20.9"), is_acceptable=True, failed=[]))
    await db_session.commit()
    refused = await hse.post(f"/api/v1/permits/{pid}/issue", json={})
    assert refused.status_code == 422 and "not complete" in refused.text


async def test_a_failing_test_stops_a_permit_that_is_out(hse_manager, confined):
    _, hse = hse_manager
    for start in (-1, 2):   # live, and issued for later
        pid = await _raise(hse, confined, start=start, end=5)
        await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)
        await _issued(hse, pid)
        await hse.post(f"/api/v1/permits/{pid}/gas", json={**ALL_FOUR, "h2s_ppm": 11})
        assert (await hse.get(f"/api/v1/permits/{pid}")).json()["status"] == "suspended"


@pytest.mark.parametrize("values,ok", [
    ({"oxygen_pct": "19.5"}, True), ({"oxygen_pct": "19.4"}, False),
    ({"oxygen_pct": "23.0"}, True), ({"oxygen_pct": "23.1"}, False),
    ({"lel_pct": "9.9"}, True), ({"lel_pct": "10"}, False),
    ({"h2s_ppm": "10"}, True), ({"h2s_ppm": "10.1"}, False),
    ({"co_ppm": "25"}, True), ({"co_ppm": "25.1"}, False),
])
def test_the_gas_limits(values, ok):
    reading = {k: Decimal(str(v)) for k, v in {**ALL_FOUR, **values}.items()}
    assert rules.assess_gas(**reading, require_all=True).acceptable is ok


def test_a_gas_test_goes_stale():
    now = _now()
    assert rules.reading_is_fresh(now - timedelta(minutes=120), now)
    assert not rules.reading_is_fresh(now - timedelta(minutes=121), now)


@pytest.mark.parametrize("role,allowed", [
    ("requester", False), ("auditor", False), ("jhsc_member", False), ("first_aider", False),
    ("area_supervisor", True), ("ehs_coordinator", True),
])
async def test_only_whoever_raised_it_or_issues_permits_adds_to_it(
        worker, test_engine, db_session, confined, role, allowed):
    _, mine = worker
    pid = await _raise(mine, confined)
    who = await make_user(test_engine, role=role)
    async with authed_client(make_token(who.id, who.role), db_session) as c:
        assert (await c.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)).status_code == (
            201 if allowed else 403)
        assert (await _sign(c, pid)).status_code == (201 if allowed else 403)
    # Whoever raised it still may.
    assert (await mine.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)).status_code == 201
    assert (await _sign(mine, pid)).status_code == 201


async def test_a_draft_has_nothing_to_close_and_a_closed_permit_takes_nothing_more(
        hse_manager, confined):
    _, hse = hse_manager
    close = {"closeout_note": "Empty", "signature": {"capacity": "issuer", **SIG}}
    pid = await _raise(hse, confined)
    assert (await hse.post(f"/api/v1/permits/{pid}/close", json=close)).status_code == 409
    await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)
    await _issued(hse, pid)
    done = await hse.post(f"/api/v1/permits/{pid}/close", json=close)
    assert done.status_code == 200 and done.json()["status"] == "closed"
    assert (await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)).status_code == 409
    assert (await _sign(hse, pid)).status_code == 409


async def test_an_expired_permit_can_be_tested_and_closed(hse_manager, confined, db_session):
    """It ran out with people possibly still inside: the one time nothing may
    stand between somebody and a gas test, or a close."""
    from app.models.permit import Permit
    _, hse = hse_manager
    pid = await _raise(hse, confined)
    await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)
    await _issued(hse, pid)
    row = await db_session.get(Permit, uuid.UUID(pid))
    row.valid_to = _now() - timedelta(minutes=1)
    await db_session.commit()
    assert (await hse.get(f"/api/v1/permits/{pid}")).json()["status"] == "expired"
    assert (await hse.post(f"/api/v1/permits/{pid}/gas", json=ALL_FOUR)).status_code == 201
    assert (await hse.post(f"/api/v1/permits/{pid}/close", json={
        "closeout_note": "Empty", "signature": {"capacity": "issuer", **SIG}})).status_code == 200
