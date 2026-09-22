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


async def test_an_owner_verifying_their_own_work_is_recorded_as_such(hse_manager, worker):
    """This used to be refused, and HSE asked for it to be allowed.

    The module was built so that whoever did the work could not sign it off —
    the second-person check COR and ISO 45001 look for. The second acceptance
    round answered our question with "every role should be able to verify an
    action if it is assigned to them", so it is allowed. That is their process
    to choose.

    What does not change is the record: the verification carries who performed
    it, so an owner verifying their own reads as exactly that in the audit pack
    rather than passing for independent.
    """
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "done", "new_status": "pending_verification"})
    r = await wclient.post(f"/api/v1/actions/{action['id']}/verify", json={"is_effective": True})
    assert r.status_code == 200, r.text

    detail = r.json()
    assert detail["status"] == "closed"
    verifier = detail["verifications"][-1]
    assert verifier["verified_by_name"] == owner.full_name, (
        "who verified has to be on the record, or a self-verification is "
        "indistinguishable from an independent one")


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
    other = await make_user(test_engine, role="requester")

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


# ── Seeing only what you are part of ────────────────────────────────────────

async def test_a_worker_sees_only_their_own_actions_in_the_list(hse_manager, worker, test_engine):
    """A worker holds ehs.action.read so they can work what is assigned to
    them. That is not the same as reading everybody's."""
    from tests.conftest import make_user
    _, mclient = hse_manager
    owner, wclient = worker
    other = await make_user(test_engine, role="requester")

    mine = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    theirs = (await mclient.post("/api/v1/actions", json=_payload(other.id))).json()

    ids = {r["id"] for r in (await wclient.get("/api/v1/actions")).json()}
    assert mine["id"] in ids
    assert theirs["id"] not in ids


async def test_a_worker_cannot_open_someone_elses_action(hse_manager, worker, test_engine):
    from tests.conftest import make_user
    _, mclient = hse_manager
    _, wclient = worker
    other = await make_user(test_engine, role="requester")
    theirs = (await mclient.post("/api/v1/actions", json=_payload(other.id))).json()

    r = await wclient.get(f"/api/v1/actions/{theirs['id']}")
    assert r.status_code == 403
    assert "assigned to you" in r.text


async def test_a_worker_cannot_report_progress_on_someone_elses_action(
    hse_manager, worker, test_engine
):
    from tests.conftest import make_user
    _, mclient = hse_manager
    _, wclient = worker
    other = await make_user(test_engine, role="requester")
    theirs = (await mclient.post("/api/v1/actions", json=_payload(other.id))).json()

    r = await wclient.post(f"/api/v1/actions/{theirs['id']}/updates", json={"body": "not mine"})
    assert r.status_code == 403


async def test_an_auditor_sees_the_whole_action_register(hse_manager, worker, auditor):
    """Auditors browse without managing — they hold the read marker."""
    _, mclient = hse_manager
    owner, _ = worker
    _, aclient = auditor
    a = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    ids = {r["id"] for r in (await aclient.get("/api/v1/actions")).json()}
    assert a["id"] in ids


async def test_the_owner_may_verify_their_own_action(hse_manager, worker):
    """HSE asked for this, and it is a change of principle worth naming.

    The module was built so that whoever did the work could not sign it off —
    the second-person check COR and ISO 45001 look for. The second acceptance
    round answered our question with "every role should be able to verify an
    action if it is assigned to them", so an owner may. The record still says
    who verified beside who owned it, so a self-verification reads as one.
    """
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()
    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "Guard fitted.", "new_status": "pending_verification"})

    verified = await wclient.post(f"/api/v1/actions/{action['id']}/verify",
                                  json={"is_effective": True, "note": "Checked on the line"})
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "closed"


async def test_somebody_elses_action_still_needs_the_permission(
    hse_manager, worker, test_engine, db_session
):
    """The other half of the answer: all CAPA items, but only for those roles."""
    from tests.conftest import authed_client, make_token, make_user

    _, mclient = hse_manager
    owner, _ = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    bystander = await make_user(test_engine, role="requester", full_name="Not Involved")
    async with authed_client(make_token(bystander.id, bystander.role), db_session) as theirs:
        r = await theirs.post(f"/api/v1/actions/{action['id']}/verify",
                              json={"is_effective": True})
        assert r.status_code == 403


async def test_handing_over_takes_it_off_your_list(hse_manager, worker):
    """It is somebody else's move once handed over.

    It used to stay in "my actions" afterwards, looking like outstanding work —
    reported in round 2 as items not leaving the screen once completed.
    """
    _, mclient = hse_manager
    owner, wclient = worker
    action = (await mclient.post("/api/v1/actions", json=_payload(owner.id))).json()

    mine = (await wclient.get("/api/v1/actions/mine")).json()
    assert any(a["id"] == action["id"] for a in mine)

    await wclient.post(f"/api/v1/actions/{action['id']}/updates",
                       json={"body": "Done.", "new_status": "pending_verification"})

    mine_after = (await wclient.get("/api/v1/actions/mine")).json()
    assert all(a["id"] != action["id"] for a in mine_after), "it is no longer yours to do"

    waiting = (await wclient.get("/api/v1/actions/mine/to-verify")).json()
    assert any(a["id"] == action["id"] for a in waiting), "but it is yours to judge"


async def test_the_work_request_number_is_set_and_never_silently_cleared(
        hse_manager, worker):
    """Most physical corrective actions are done by maintenance.

    Guard the pinch point, replace the eyewash, clear the exit — all of it goes
    through maintenance under their own number, in their own system. Without it
    recorded here the two records cannot be tied together and "has it been
    done" is answered by asking somebody, which is what the CAPA then closes on.
    """
    from datetime import date, timedelta

    _, client = hse_manager
    owner, _ = worker
    action = (await client.post("/api/v1/actions", json={
        "source_type": "manual", "source_id": None,
        "title": "Guard the case sealer infeed",
        "owner_id": str(owner.id),
        "due_date": (date.today() + timedelta(days=14)).isoformat(),
    })).json()
    assert action["work_request_no"] is None

    await client.post(f"/api/v1/actions/{action['id']}/updates", json={
        "body": "Raised with maintenance.",
        "work_request_no": "WR-44812",
    })
    after = (await client.get(f"/api/v1/actions/{action['id']}")).json()
    assert after["work_request_no"] == "WR-44812"

    # A later note that says nothing about the work request must leave it
    # alone. Blank means "no change", not "there is no work request any more".
    await client.post(f"/api/v1/actions/{action['id']}/updates", json={
        "body": "Guard on order, fitting Friday.",
    })
    later = (await client.get(f"/api/v1/actions/{action['id']}")).json()
    assert later["work_request_no"] == "WR-44812", (
        "an update that did not mention the work request must not clear it")
