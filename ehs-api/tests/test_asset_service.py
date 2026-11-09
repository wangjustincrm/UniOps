"""★ Round 10 (open box): "Failing it takes the item out of service and raises
a corrective action and should also list the item as being out of service on
the my actions for all staff to read … when the safety equipment capa has been
completed and verified, put the equipment back into service and log the date
and time and by whom on the equipment register."
"""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.models.action import Action
from app.services.day_range import plant_today


async def _asset(hse, name="Eyewash 3"):
    made = await hse.post("/api/v1/safety-assets", json={"name": name})
    assert made.status_code == 201, made.text
    return made.json()


async def _fail(client, asset_id, notes="Bowl cracked", days_ago=0):
    got = await client.post(f"/api/v1/safety-assets/{asset_id}/inspections", json={
        "result": "failed", "notes": notes,
        "performed_on": (plant_today() - timedelta(days=days_ago)).isoformat()})
    assert got.status_code == 201, got.text


async def _done(client, verifier, action_id):
    await client.post(f"/api/v1/actions/{action_id}/updates",
                      json={"body": "Replaced the bowl", "new_status": "pending_verification"})
    got = await verifier.post(f"/api/v1/actions/{action_id}/verify",
                              json={"is_effective": True, "evidence": "Flow tested"})
    assert got.status_code == 200, got.text


async def _actions_for(db, asset_id):
    db.expire_all()
    return list((await db.execute(select(Action).where(
        Action.asset_id == uuid.UUID(asset_id)))).scalars())


async def test_a_failed_check_takes_it_out_and_raises_one_action(hse_manager, db_session):
    user, hse = hse_manager
    a = await _asset(hse)
    await _fail(hse, a["id"])
    item = (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()
    assert item["status"] == "out_of_service"
    [action] = await _actions_for(db_session, a["id"])
    assert action.source_type == "asset" and action.source_ref == item["asset_no"]
    assert action.priority == "high" and "Repair or replace Eyewash 3" in action.title
    assert action.due_date == plant_today() + timedelta(days=7)
    [event] = (await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()
    assert event["status"] == "out_of_service" and event["by_name"] == user.full_name
    assert "Bowl cracked" in event["reason"]

    # Failing again while it is out raises no second action, and no second row.
    await _fail(hse, a["id"], notes="Still cracked", days_ago=1)
    assert len(await _actions_for(db_session, a["id"])) == 1
    assert len((await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()) == 1


async def test_back_in_service_only_once_every_action_is_verified(hse_manager, db_session):
    user, hse = hse_manager
    a = await _asset(hse)
    await _fail(hse, a["id"])
    [first] = await _actions_for(db_session, a["id"])
    # A second action on the same item (as a checklist with two failures raises).
    second = (await hse.post("/api/v1/actions", json={
        "source_type": "asset", "source_id": a["id"], "title": "Re-plumb the supply",
        "owner_id": str(user.id), "due_date": plant_today().isoformat()})).json()
    row = await db_session.get(Action, uuid.UUID(second["id"]))
    row.asset_id = uuid.UUID(a["id"])
    await db_session.flush()

    await _done(hse, hse, str(first.id))
    assert (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()["status"] == "out_of_service", \
        "one of two actions done is not the item fixed"
    await _done(hse, hse, second["id"])
    assert (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()["status"] == "in_service"
    latest = (await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()[0]
    assert latest["status"] == "in_service" and latest["by_name"] == user.full_name
    assert latest["action_id"] == second["id"] and second["action_no"] in latest["reason"]


async def test_a_failed_checklist_takes_it_out_too(hse_manager, db_session):
    """★ The checklist path stored 'fail', which the out-of-service rule never
    matched, so a failed checklist left its item in service. End to end: the
    item's own form, a failed answer, submitted. The actions are the
    checklist's own (one per failed answer), and they are about the item."""
    _, hse = hse_manager
    t = (await hse.post("/api/v1/forms/templates", json={
        "code": f"eye-{uuid.uuid4().hex[:5]}", "name": "Eyewash check",
        "category": "inspection"})).json()
    assert (await hse.put(f"/api/v1/forms/templates/{t['id']}/schema", json={"schema": [
        {"key": "flow", "type": "yes_no_na", "label": "Does it flow clear?",
         "fail_on": {"equals": "no"}}]})).status_code in (200, 201)
    assert (await hse.post(f"/api/v1/forms/templates/{t['id']}/publish",
                           json={"change_note": "v1"})).status_code in (200, 201)
    a = (await hse.post("/api/v1/safety-assets", json={
        "name": "Eyewash 9", "inspection_template_id": t["id"]})).json()
    started = await hse.post(f"/api/v1/safety-assets/{a['id']}/inspection-form/start")
    assert started.status_code == 200, started.text
    sid = started.json()["submission_id"]
    assert (await hse.put(f"/api/v1/forms/submissions/{sid}/answers",
                          json={"values": {"flow": "no"}})).status_code == 200
    done = await hse.post(f"/api/v1/forms/submissions/{sid}/submit", json={})
    assert done.status_code == 200, done.text

    assert (await hse.get(f"/api/v1/safety-assets/{a['id']}")).json()["status"] == "out_of_service"
    [check] = (await hse.get("/api/v1/safety-assets/inspections",
                             params={"asset_id": a["id"]})).json()
    assert check["result"] == "failed"
    [action] = await _actions_for(db_session, a["id"])
    assert action.source_type == "inspection", "the checklist's own action, not a second one"


async def test_put_back_by_hand_says_why(hse_manager, worker):
    user, hse = hse_manager
    _, someone = worker
    a = await _asset(hse)
    await _fail(hse, a["id"])
    url = f"/api/v1/safety-assets/{a['id']}/return-to-service"
    assert (await hse.post(url, json={})).status_code == 422
    assert (await someone.post(url, json={"reason": "Looks fine to me"})).status_code == 403
    assert (await hse.post(url, json={"reason": "Bowl replaced by maintenance"})).status_code == 200
    latest = (await hse.get(f"/api/v1/safety-assets/{a['id']}/history")).json()[0]
    assert latest["status"] == "in_service" and latest["reason"] == "Bowl replaced by maintenance"
    assert latest["by_name"] == user.full_name and latest["action_id"] is None


async def test_everyone_reads_what_is_out_of_service(hse_manager, worker):
    _, hse = hse_manager
    _, someone = worker
    a = await _asset(hse, name=f"Extinguisher {uuid.uuid4().hex[:4]}")
    await _fail(hse, a["id"])
    rows = (await someone.get("/api/v1/safety-assets/out-of-service")).json()
    [mine] = [r for r in rows if r["id"] == a["id"]]
    assert mine["name"] == a["name"] and mine["since"]
    assert set(mine) == {"id", "asset_no", "name", "location_path", "since"}


async def test_retire_takes_it_off_its_cycle_and_delete_only_an_unused_item(hse_manager, db_session):
    """★ Round 12, Q3: "Include this function on all equipment and safety items"."""
    from app.crud.asset import generate_due
    from app.models.asset import AssetInspection
    from app.models.mirrors import Task

    _, client = hse_manager
    a = await _asset(client, f"Extinguisher {uuid.uuid4().hex[:5]}")
    await client.patch(f"/api/v1/safety-assets/{a['id']}", json={
        "inspection_frequency": "monthly", "next_due_on": plant_today().isoformat()})
    await generate_due(db_session)
    await db_session.commit()
    owed = list((await db_session.execute(select(AssetInspection.id).where(
        AssetInspection.asset_id == uuid.UUID(a["id"])))).scalars())
    assert owed, "precondition: a check is owed"

    retired = await client.post(f"/api/v1/safety-assets/{a['id']}/retire",
                                json={"reason": "Replaced with a new unit"})
    assert retired.status_code == 200 and retired.json()["status"] == "retired", retired.text
    db_session.expire_all()
    assert not list((await db_session.execute(select(AssetInspection.id).where(
        AssetInspection.asset_id == uuid.UUID(a["id"])))).scalars()), "the owed check goes"
    open_tasks = (await db_session.execute(select(Task).where(
        Task.document_id.in_(owed), Task.is_completed.is_(False)))).scalars().all()
    assert not open_tasks, "and so does its task"
    history = (await client.get(f"/api/v1/safety-assets/{a['id']}/history")).json()
    assert history[0]["status"] == "retired" and history[0]["reason"] == "Replaced with a new unit"

    # It has a history now, so it cannot be deleted.
    refused = await client.delete(f"/api/v1/safety-assets/{a['id']}")
    assert refused.status_code == 409 and "status changes" in refused.text, refused.text

    fresh = await _asset(client, f"Typo {uuid.uuid4().hex[:5]}")
    assert (await client.delete(f"/api/v1/safety-assets/{fresh['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/safety-assets/{fresh['id']}")).status_code == 404
