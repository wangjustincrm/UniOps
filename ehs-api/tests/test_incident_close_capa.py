"""An incident is not closed while a corrective action on it is still open.

Round 6, answer box 1: "when an incident has a capa that is not closed, do not
allow the incident to be closed."
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy

from tests.conftest import make_user


async def _incident(client) -> str:
    made = await client.post("/api/v1/incidents", json={
        "form_kind": "equipment", "title": "Forklift into the racking",
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "incident_type": "property_damage"})
    iid = made.json()["id"]
    await client.post(f"/api/v1/incidents/{iid}/submit")
    await client.post(f"/api/v1/incidents/{iid}/hse-review")
    return iid


async def test_an_open_action_holds_the_incident_open(hse_manager, test_engine, db_session):
    _, client = hse_manager
    iid = await _incident(client)
    owner = await make_user(test_engine, role="area_supervisor")
    action = (await client.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid,
        "title": "Re-stripe the aisle", "hierarchy_of_control": "engineering",
        "owner_id": str(owner.id),
        "due_date": (date.today() + timedelta(days=7)).isoformat()})).json()
    assert action["status"] == "open"

    refused = await client.post(f"/api/v1/incidents/{iid}/close")
    assert refused.status_code == 409, refused.text
    assert action["action_no"] in refused.text and "still open" in refused.text

    # Done — or cancelled — and it may close.
    await db_session.execute(sqlalchemy.text(
        "UPDATE ehs_actions SET status = 'cancelled' WHERE id = :i"), {"i": uuid.UUID(action["id"])})
    closed = await client.post(f"/api/v1/incidents/{iid}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"


async def test_with_no_actions_it_closes(hse_manager):
    """The admission beside the refusal."""
    _, client = hse_manager
    iid = await _incident(client)
    assert (await client.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
