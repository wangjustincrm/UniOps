"""★ Round 12, batch 3d: corrective actions.

Q7 only supervisors and HSE reassign (and verify — see test_action_flow);
Q5 HSE is asked to review a record once its last action closes; JHSC-01 the
record shows each action's history.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.mirrors import Task
from tests.conftest import authed_client, make_token, make_user

DUE = (date.today() + timedelta(days=3)).isoformat()


async def _incident(hse):
    made = await hse.post("/api/v1/incidents", json={
        "form_kind": "near_miss", "title": "Pallet left in the walkway",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "incident_type": "near_miss"})
    iid = made.json()["id"]
    await hse.post(f"/api/v1/incidents/{iid}/submit")
    return iid


async def _action(hse, owner_id, iid, title):
    made = await hse.post("/api/v1/actions", json={
        "source_type": "incident", "source_id": iid, "title": title,
        "owner_id": str(owner_id), "due_date": DUE})
    assert made.status_code == 201, made.text
    return made.json()["id"]


async def _finish(owner_client, hse, aid):
    await owner_client.post(f"/api/v1/actions/{aid}/updates",
                            json={"body": "Done", "new_status": "pending_verification"})
    got = await hse.post(f"/api/v1/actions/{aid}/verify",
                         json={"is_effective": True, "evidence": "Walked it"})
    assert got.status_code == 200, got.text


async def _reviews(db, iid):
    db.expire_all()
    return list((await db.execute(select(Task).where(
        Task.type == "ehs_review_capas", Task.document_id == uuid.UUID(iid)))).scalars())


async def test_only_supervisors_and_hse_reassign_and_the_task_moves(hse_manager, worker,
                                                                    test_engine, db_session):
    _, hse = hse_manager
    owner, w = worker
    other = await make_user(test_engine, role="requester", full_name="Second Pair")
    iid = await _incident(hse)
    aid = await _action(hse, owner.id, iid, "Move the pallet store")

    refused = await w.post(f"/api/v1/actions/{aid}/reassign",
                           json={"owner_id": str(other.id), "reason": "Not my area"})
    assert refused.status_code == 403, "a worker does not reassign their own"

    moved = await hse.post(f"/api/v1/actions/{aid}/reassign",
                           json={"owner_id": str(other.id), "reason": "Warehouse team does this"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner_name"] == "Second Pair"
    db_session.expire_all()
    open_tasks = (await db_session.execute(select(Task.assigned_user_id).where(
        Task.document_id == uuid.UUID(aid), Task.type == "ehs_do_action",
        Task.is_completed.is_(False)))).scalars().all()
    assert open_tasks == [other.id], "the open task goes with the work"

    history = (await hse.get("/api/v1/actions/history",
                             params={"source_type": "incident", "source_id": iid})).json()
    whats = [e["what"] for e in history[0]["entries"]]
    assert whats[:2] == ["raised", "reassigned"]
    assert "Warehouse team does this" in history[0]["entries"][1]["text"]


async def test_hse_is_asked_to_review_once_the_last_action_closes(hse_manager, worker, db_session):
    _, hse = hse_manager
    owner, w = worker
    iid = await _incident(hse)
    first = await _action(hse, owner.id, iid, "Paint the walkway lines")
    second = await _action(hse, owner.id, iid, "Brief the night shift")

    await _finish(w, hse, first)
    assert await _reviews(db_session, iid) == [], "one is still open"
    await _finish(w, hse, second)
    reviews = await _reviews(db_session, iid)
    assert len(reviews) == 1 and reviews[0].assigned_role == "ehs_manager"
    assert reviews[0].document_type == "ehs_inc"

    where = (await hse.get(f"/api/v1/actions/tasks/{reviews[0].id}/open")).json()
    assert where["path"] == f"/incidents/{iid}", "the task opens the record"

    assert (await w.post(f"/api/v1/actions/tasks/{reviews[0].id}/reviewed")).status_code == 403
    assert (await hse.post(f"/api/v1/actions/tasks/{reviews[0].id}/reviewed")).status_code == 204
    assert all(t.is_completed for t in await _reviews(db_session, iid))

    history = (await hse.get("/api/v1/actions/history",
                             params={"source_type": "incident", "source_id": iid})).json()
    assert [h["status"] for h in history] == ["closed", "closed"]
    assert [e["what"] for e in history[0]["entries"]] == ["raised", "update", "verified"]
