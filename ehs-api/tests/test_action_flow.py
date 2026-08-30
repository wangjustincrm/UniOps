"""Corrective actions, and the inbox tasks that make them visible.

An action that exists but that its owner never sees is the failure this module
is meant to remove, so most of these assert on the task rows as much as on the
action itself.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy

from app.services import tasks as task_service

TOMORROW = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()


def _payload(owner_id, **kw) -> dict:
    body = {
        "source_type": "manual",
        "title": "Fit an interlocked guard to the capping head",
        "description": "Guard is removed to clear jams and not refitted.",
        "hierarchy_of_control": "engineering",
        "owner_id": str(owner_id),
        "due_date": TOMORROW,
    }
    body.update(kw)
    return body


async def _tasks_for(db_session, action_id) -> list[dict]:
    rows = (await db_session.execute(sqlalchemy.text(
        "SELECT type, assigned_user_id, assigned_role, is_completed, priority"
        " FROM tasks WHERE document_id = :d ORDER BY created_at"
    ), {"d": str(action_id)})).mappings().all()
    return [dict(r) for r in rows]


# ── Raising ─────────────────────────────────────────────────────────────────

async def test_raising_an_action_puts_it_in_the_owners_inbox(hse_manager, worker, db_session):
    _, mclient = hse_manager
    owner, _ = worker
    body = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    assert body["action_no"].startswith("CAPA-")
    assert body["status"] == "open"
    assert body["owner_name"] == owner.full_name

    tasks = await _tasks_for(db_session, body["id"])
    assert len(tasks) == 1
    assert tasks[0]["type"] == task_service.TASK_DO_ACTION
    assert str(tasks[0]["assigned_user_id"]) == str(owner.id)
    assert tasks[0]["is_completed"] is False


async def test_the_task_type_is_never_mistaken_for_an_approval(hse_manager, worker, db_session):
    """epms-api refuses to complete a task whose type starts with 'approve',
    so one written here under such a name could never be closed."""
    _, mclient = hse_manager
    owner, _ = worker
    body = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    for task in await _tasks_for(db_session, body["id"]):
        assert not task["type"].startswith("approve")


async def test_a_sourced_action_requires_its_source_id(hse_manager, worker):
    _, mclient = hse_manager
    owner, _ = worker
    r = await mclient.post("/api/v1/actions", json=_payload(owner.id, source_type="incident"))
    assert r.status_code == 422
    assert "source_id" in r.text


async def test_a_worker_cannot_raise_an_action(worker):
    owner, wclient = worker
    r = await wclient.post("/api/v1/actions", json=_payload(owner.id))
    assert r.status_code == 403


# ── Doing the work ──────────────────────────────────────────────────────────

async def test_the_owner_can_report_progress(hse_manager, worker):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    r = await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                           json={"body": "Guard ordered, arriving Tuesday.",
                                 "new_status": "in_progress"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert [u["body"] for u in body["updates"]] == ["Guard ordered, arriving Tuesday."]
    assert body["updates"][0]["author_name"] == owner.full_name


async def test_marking_it_done_hands_over_to_a_verifier(hse_manager, worker, db_session):
    """The doer's task closes and a verification task opens for someone else —
    confirming the control works is a different job from doing the work."""
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    body = (await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                               json={"body": "Guard fitted and tested.",
                                     "new_status": "pending_verification"})).json()
    assert body["status"] == "pending_verification"

    tasks = await _tasks_for(db_session, action["id"])
    do_tasks = [t for t in tasks if t["type"] == task_service.TASK_DO_ACTION]
    verify_tasks = [t for t in tasks if t["type"] == task_service.TASK_VERIFY_ACTION]
    assert all(t["is_completed"] for t in do_tasks)
    assert len(verify_tasks) == 1
    assert verify_tasks[0]["is_completed"] is False
    assert verify_tasks[0]["assigned_role"] == "ehs_coordinator"


# ── Verification ────────────────────────────────────────────────────────────

async def test_an_effective_verification_closes_it_and_clears_the_inbox(
    hse_manager, worker, db_session
):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})

    body = (await mclient.post(f"/api/v1/actions/{action['id']}/verify",
                               json={"is_effective": True,
                                     "evidence": "Guard interlock tested on all three heads."})).json()
    assert body["status"] == "closed"
    assert body["closed_at"] is not None
    assert body["verifications"][0]["is_effective"] is True

    assert all(t["is_completed"] for t in await _tasks_for(db_session, action["id"]))


async def test_a_failed_verification_reopens_it_with_a_new_task(hse_manager, worker, db_session):
    """The reason verification exists at all: COR asks for evidence that a
    control was effective, not that somebody did something."""
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})

    body = (await mclient.post(f"/api/v1/actions/{action['id']}/verify",
                               json={"is_effective": False,
                                     "evidence": "Interlock can still be defeated with a magnet."})).json()
    assert body["status"] == "in_progress"
    assert body["closed_at"] is None
    assert body["verifications"][0]["is_effective"] is False

    tasks = await _tasks_for(db_session, action["id"])
    open_tasks = [t for t in tasks if not t["is_completed"]]
    assert len(open_tasks) == 1
    assert open_tasks[0]["type"] == task_service.TASK_DO_ACTION
    assert open_tasks[0]["priority"] == "high"
    assert str(open_tasks[0]["assigned_user_id"]) == str(owner.id)


async def test_a_worker_cannot_verify_their_own_work(hse_manager, worker):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    r = await wclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})
    assert r.status_code == 403


async def test_verifying_a_closed_action_is_refused(hse_manager, worker):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    await mclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})
    again = await mclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})
    assert again.status_code == 409


async def test_updating_a_closed_action_is_refused(hse_manager, worker):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    await mclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})
    r = await wclient.post(f"/api/v1/actions/{action['id']}/updates", json={"body": "one more thing"})
    assert r.status_code == 409


# ── Listing ─────────────────────────────────────────────────────────────────

async def test_mine_returns_only_the_callers_open_actions(hse_manager, worker, test_engine):
    from tests.conftest import make_user
    _, mclient = hse_manager
    owner, wclient = worker
    other = await make_user(test_engine, role="worker")

    ours = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    theirs = (await mclient.post("/api/v1/actions", json=_payload(other.id))).json()

    rows = (await wclient.get("/api/v1/actions/mine")).json()
    ids = {r["id"] for r in rows}
    assert ours["id"] in ids
    assert theirs["id"] not in ids


async def test_mine_hides_closed_actions_unless_asked(hse_manager, worker):
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    await mclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})

    assert action["id"] not in {r["id"] for r in (await wclient.get("/api/v1/actions/mine")).json()}
    with_closed = await wclient.get("/api/v1/actions/mine", params={"include_closed": "true"})
    assert action["id"] in {r["id"] for r in with_closed.json()}


async def test_overdue_filter_finds_only_past_due_open_actions(hse_manager, worker):
    _, mclient = hse_manager
    owner, _ = worker
    past = (datetime.now(timezone.utc).date() - timedelta(days=6)).isoformat()
    late = (await mclient.post("/api/v1/actions", json=_payload(owner.id, due_date=past))).json()
    ontime = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    ids = {r["id"] for r in (await mclient.get("/api/v1/actions", params={"overdue": "true"})).json()}
    assert late["id"] in ids
    assert ontime["id"] not in ids


async def test_unknown_action_is_404(hse_manager):
    _, client = hse_manager
    assert (await client.get(f"/api/v1/actions/{uuid.uuid4()}")).status_code == 404
