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


async def test_an_action_remembers_which_document_raised_it(hse_manager, worker):
    """★ "Raised from INC-…" is the first thing anyone reading an action wants
    to open, and it was blank for nearly all of them.

    `source_ref` is not a field on `ActionCreate`, so an action raised through
    the API stored none — which is every action raised from a screen. Only the
    few backend paths that passed one explicitly had it, and the rest offered
    no way back to what they were about.
    """
    owner, _ = worker
    _, client = hse_manager

    incident = (await client.post("/api/v1/incidents", json={
        "form_kind": "medical", "title": "Caught a hand at the capping head",
        "incident_type": "first_aid",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "description": "Guard was off.",
        "persons": [{"role": "injured", "person_name": "Dario Okafor"}],
    })).json()

    action = (await client.post("/api/v1/actions", json=_payload(
        owner.id, source_type="incident", source_id=incident["id"]))).json()

    assert action["source_ref"] == incident["incident_no"], (
        "the action has to carry the number of the document it came from, or "
        "the screen showing it has nothing to print and nowhere to link")
    assert action["source_id"] == incident["id"]


async def test_an_action_with_no_numbered_source_says_nothing_rather_than_guessing(
        hse_manager, worker):
    """A manually raised action has no source document, and inventing a
    reference for it would be worse than leaving the line off the page."""
    owner, _ = worker
    _, client = hse_manager
    action = (await client.post("/api/v1/actions", json=_payload(owner.id))).json()
    assert action["source_type"] == "manual"
    assert action["source_ref"] is None


async def test_a_task_given_to_a_role_reaches_the_people_holding_it(hse_manager, db_session):
    """★ `assigned_role` was written by every caller in this module and read by
    none of them.

    A task raised for "the HSE Manager" rather than for a named person went to
    the `tasks` table and appeared on nobody's screen. On the test database
    that was seventy-eight open tasks nobody could see — incidents awaiting
    review, inspections due, SDS reviews — which is precisely the failure this
    page exists to prevent.
    """
    import uuid as _uuid

    from app.services import tasks as task_service

    manager, client = hse_manager
    doc_id = _uuid.uuid4()
    await task_service.open_task(
        db_session,
        task_type="ehs_do_action",
        doc_type="ehs_jhsc",
        doc_id=doc_id,
        doc_number="JHSC-2026-0001",
        title="Answer the committee's recommendation",
        assigned_role=manager.role,
        assigned_user_id=None,
        description="Twenty-one days from the date it was raised.",
    )
    await db_session.commit()

    mine = await client.get("/api/v1/actions/mine/tasks")
    assert mine.status_code == 200, mine.text
    assert str(doc_id) in {t["document_id"] for t in mine.json()}


async def test_a_role_task_does_not_reach_somebody_who_holds_another(worker, db_session):
    """And it reaches the role it was given to, not everybody.

    Without this the admission above would be satisfied by a page that shows
    every unassigned task in the plant to whoever opens it.
    """
    import uuid as _uuid

    from app.services import tasks as task_service

    _, client = worker
    doc_id = _uuid.uuid4()
    await task_service.open_task(
        db_session,
        task_type="ehs_do_action",
        doc_type="ehs_jhsc",
        doc_id=doc_id,
        doc_number="JHSC-2026-0002",
        title="For the HSE Manager only",
        assigned_role="ehs_manager",
        assigned_user_id=None,
    )
    await db_session.commit()

    mine = await client.get("/api/v1/actions/mine/tasks")
    assert mine.status_code == 200, mine.text
    assert str(doc_id) not in {t["document_id"] for t in mine.json()}


async def test_a_supervisor_sees_the_escalation_about_somebody_elses_action(
        hse_manager, worker, db_session):
    """★ The overdue notice a supervisor is sent reached no screen.

    My Actions listed corrective actions by owner, and dropped every task on a
    corrective action underneath on the grounds that they were already in that
    list. A supervisor is not the owner, so "Overdue 12d: …" was excluded from
    the one page it was supposed to arrive on.
    """
    import uuid as _uuid

    from app.services import tasks as task_service

    owner, _ = worker
    supervisor, client = hse_manager
    action = (await client.post("/api/v1/actions", json=_payload(owner.id))).json()

    await task_service.open_task(
        db_session,
        task_type=task_service.TASK_DO_ACTION,
        doc_type=task_service.DOC_ACTION,
        doc_id=_uuid.UUID(action["id"]),
        doc_number=action["action_no"],
        title="Overdue 12d: Fit an interlocked guard to the capping head",
        assigned_role="area_supervisor",
        assigned_user_id=supervisor.id,
        description="The owner has not closed this.",
        priority="high",
    )
    await db_session.commit()

    mine = await client.get("/api/v1/actions/mine/tasks")
    assert mine.status_code == 200, mine.text
    assert action["id"] in {t["document_id"] for t in mine.json()}, (
        "the escalation is about somebody else's action, so nothing else on "
        "this page is showing it")


async def test_the_owner_does_not_see_their_own_action_twice(worker, hse_manager):
    """And the reason the exclusion exists still holds: an action you own is
    listed once, above, not again underneath as a task."""
    owner, owner_client = worker
    _, client = hse_manager
    action = (await client.post("/api/v1/actions", json=_payload(owner.id))).json()

    listed = (await owner_client.get("/api/v1/actions/mine")).json()
    assert action["id"] in {a["id"] for a in listed}, "it is theirs to do"

    tasks = (await owner_client.get("/api/v1/actions/mine/tasks")).json()
    assert action["id"] not in {t["document_id"] for t in tasks}


# ── Whose part of the plant ─────────────────────────────────────────────────

async def _an_area(db_session, path: str):
    """A node of the location tree, with its live path."""
    import uuid as _uuid

    from sqlalchemy import text as sql_text

    loc_id = _uuid.uuid4()
    await db_session.execute(sql_text(
        "insert into locations (id, code, name, level, path, depth, is_active)"
        " values (:id, :code, :name, 'department', :path, 1, true)"),
        {"id": loc_id, "code": path[:40], "name": path.split('/')[-1], "path": path})
    await db_session.commit()
    return loc_id


async def test_an_area_task_reaches_the_supervisor_who_covers_it(
        hse_manager, test_engine, db_session):
    """★ Work given to `area_supervisor` used to reach every supervisor at once.

    A queue everybody can see is a queue nobody owns, which is what the plant
    reported. It now goes to whoever is down for the area it happened in.
    """
    import uuid as _uuid

    from sqlalchemy import text as sql_text

    from app.models.area_owner import AreaOwner
    from app.services import tasks as task_service
    from tests.conftest import authed_client, make_token, make_user

    dryer = await _an_area(db_session, "Production/103 – Dryer")
    warehouse = await _an_area(db_session, "Production/105 – Warehouse")

    ours = await make_user(test_engine, role="area_supervisor")
    theirs = await make_user(test_engine, role="area_supervisor")
    db_session.add(AreaOwner(id=_uuid.uuid4(), user_id=ours.id, user_name=ours.full_name,
                             location_id=dryer, location_path="Production/103 – Dryer"))
    db_session.add(AreaOwner(id=_uuid.uuid4(), user_id=theirs.id, user_name=theirs.full_name,
                             location_id=warehouse,
                             location_path="Production/105 – Warehouse"))

    # An asset in the dryer, and the check nobody has been named for.
    asset_id = _uuid.uuid4()
    await db_session.execute(sql_text(
        "insert into ehs_safety_assets (id, asset_no, name, location_id, status)"
        " values (:id, :no, 'Extinguisher', :loc, 'in_service')"),
        {"id": asset_id, "no": f"EQ-{str(asset_id)[:6]}", "loc": dryer})
    await task_service.open_task(
        db_session,
        task_type="ehs_do_action",
        doc_type="ehs_asset",
        doc_id=asset_id,
        doc_number="EQ-TEST",
        title="Check the extinguisher by the dryer",
        assigned_role="area_supervisor",
        assigned_user_id=None,
    )
    await db_session.commit()

    async with authed_client(make_token(ours.id, ours.role), db_session) as c:
        mine = (await c.get("/api/v1/actions/mine/tasks")).json()
        assert str(asset_id) in {t["document_id"] for t in mine}, (
            "the dryer is theirs, so the check is theirs")

    async with authed_client(make_token(theirs.id, theirs.role), db_session) as c:
        others = (await c.get("/api/v1/actions/mine/tasks")).json()
        assert str(asset_id) not in {t["document_id"] for t in others}, (
            "the warehouse supervisor is not responsible for the dryer")

    # ★ And HSE does *not* see it, because somebody is down for it. Handing
    # them every area task as well would rebuild the same undifferentiated
    # queue one desk along; they get the work nobody is named for, which is a
    # different list and a shorter one.
    _, hse = hse_manager
    seen = (await hse.get("/api/v1/actions/mine/tasks")).json()
    assert str(asset_id) not in {t["document_id"] for t in seen}
    counted = (await hse.get("/api/v1/actions/unattributed")).json()
    assert not any("dryer" in e.lower() for e in counted["examples"]), (
        "it has an owner, so it is not unattributed")


async def test_work_in_an_area_nobody_covers_falls_to_hse_and_is_counted(
        hse_manager, test_engine, db_session):
    """Not to every supervisor, and not to nobody.

    A task no screen shows is the failure this module exists to prevent; a task
    on every screen is the queue the plant asked us to remove. So it goes to
    HSE, and the number is what says the mapping is unfinished.
    """
    import uuid as _uuid

    from sqlalchemy import text as sql_text

    from app.services import tasks as task_service
    from tests.conftest import authed_client, make_token, make_user

    orphan_area = await _an_area(db_session, "Production/106 – Utilities")
    supervisor = await make_user(test_engine, role="area_supervisor")

    asset_id = _uuid.uuid4()
    await db_session.execute(sql_text(
        "insert into ehs_safety_assets (id, asset_no, name, location_id, status)"
        " values (:id, :no, 'Eyewash', :loc, 'in_service')"),
        {"id": asset_id, "no": f"EQ-{str(asset_id)[:6]}", "loc": orphan_area})
    await task_service.open_task(
        db_session,
        task_type="ehs_do_action",
        doc_type="ehs_asset",
        doc_id=asset_id,
        doc_number="EQ-ORPHAN",
        title="Check the eyewash in utilities",
        assigned_role="area_supervisor",
        assigned_user_id=None,
    )
    await db_session.commit()

    async with authed_client(make_token(supervisor.id, supervisor.role), db_session) as c:
        theirs = (await c.get("/api/v1/actions/mine/tasks")).json()
        assert str(asset_id) not in {t["document_id"] for t in theirs}

    _, hse = hse_manager
    seen = (await hse.get("/api/v1/actions/mine/tasks")).json()
    assert str(asset_id) in {t["document_id"] for t in seen}, "it falls to HSE"

    counted = (await hse.get("/api/v1/actions/unattributed")).json()
    assert counted["count"] >= 1
    assert any("eyewash" in e.lower() for e in counted["examples"])

    # And it is not a number anybody can ask for: it names documents.
    async with authed_client(make_token(supervisor.id, supervisor.role), db_session) as c:
        assert (await c.get("/api/v1/actions/unattributed")).json()["count"] == 0


async def test_the_statutory_clocks_are_not_narrowed_by_area(hse_manager, db_session):
    """★ Only `area_supervisor` work is narrowed.

    The statutory tasks are the HSE Manager's and hang off SDS revisions,
    committee recommendations and certificates — none of which have a location.
    Narrowing those would file every one as unattributed and drown the number
    that means "somebody still has to be named for this part of the plant".
    """
    import uuid as _uuid

    from app.services import tasks as task_service

    _, client = hse_manager
    doc_id = _uuid.uuid4()
    await task_service.open_task(
        db_session,
        task_type=task_service.TASK_STATUTORY,
        doc_type="ehs_sds",
        doc_id=doc_id,
        doc_number="CHM-2026-0009",
        title="sds 3y — overdue",
        assigned_role="ehs_manager",
        assigned_user_id=None,
    )
    await db_session.commit()

    mine = (await client.get("/api/v1/actions/mine/tasks")).json()
    assert str(doc_id) in {t["document_id"] for t in mine}

    counted = (await client.get("/api/v1/actions/unattributed")).json()
    assert not any("sds 3y" in e.lower() for e in counted["examples"]), (
        "a task that was never area work is not unattributed area work")


async def test_an_area_owner_who_cannot_be_reached_does_not_count_as_cover(
        hse_manager, test_engine, db_session):
    """★ The failure this whole mechanism exists to remove, created by its own
    maintenance screen.

    Area work is raised for `area_supervisor` and fetched only for holders of
    that role. Assign an area to somebody without it and the area looks
    covered — so the work stops falling to HSE — while never reaching them
    either. Nobody sees it and the unattributed count says zero.
    """
    import uuid as _uuid

    from sqlalchemy import text as sql_text

    from app.models.area_owner import AreaOwner
    from app.services import tasks as task_service
    from tests.conftest import authed_client, make_token, make_user

    area = await _an_area(db_session, "Production/107 – Office")
    # A real person, with a real job, that is not this one. `supervisor` rather
    # than `dept_manager` because this test reads their own task list, and that
    # needs `ehs.incident.report` — a role without it answers 403 and the
    # assertion would pass for the wrong reason.
    clerk = await make_user(test_engine, role="supervisor")
    db_session.add(AreaOwner(id=_uuid.uuid4(), user_id=clerk.id, user_name=clerk.full_name,
                             location_id=area, location_path="Production/107 – Office"))

    asset_id = _uuid.uuid4()
    await db_session.execute(sql_text(
        "insert into ehs_safety_assets (id, asset_no, name, location_id, status)"
        " values (:id, :no, 'First aid kit', :loc, 'in_service')"),
        {"id": asset_id, "no": f"EQ-{str(asset_id)[:6]}", "loc": area})
    await task_service.open_task(
        db_session,
        task_type="ehs_do_action",
        doc_type="ehs_asset",
        doc_id=asset_id,
        doc_number="EQ-OFFICE",
        title="Check the first aid kit in the office",
        assigned_role="area_supervisor",
        assigned_user_id=None,
    )
    await db_session.commit()

    # Not theirs: they do not hold the role the work was addressed to.
    async with authed_client(make_token(clerk.id, clerk.role), db_session) as c:
        assert str(asset_id) not in {
            t["document_id"] for t in (await c.get("/api/v1/actions/mine/tasks")).json()}

    # So it is unattributed, and HSE has it.
    _, hse = hse_manager
    assert str(asset_id) in {
        t["document_id"] for t in (await hse.get("/api/v1/actions/mine/tasks")).json()}, (
        "an assignment nobody can be reached about is not cover")
    counted = (await hse.get("/api/v1/actions/unattributed")).json()
    assert any("first aid kit" in e.lower() for e in counted["examples"])


async def test_the_screen_refuses_an_assignment_that_would_reach_nobody(
        hse_manager, test_engine, db_session):
    """And it is refused at the point of being made, with the reason.

    Better than letting somebody save it and discover months later that an area
    of the plant has been quietly answering to no one.
    """
    from tests.conftest import make_user

    _, client = hse_manager
    area = await _an_area(db_session, "Production/108 – Yard")
    clerk = await make_user(test_engine, role="dept_manager")
    supervisor = await make_user(test_engine, role="area_supervisor")

    refused = await client.post("/api/v1/settings/area-owners", json={
        "user_id": str(clerk.id), "location_id": str(area)})
    assert refused.status_code == 409, refused.text
    assert "not an area supervisor" in refused.json()["detail"]

    # The admission side: somebody who does hold it is accepted.
    ok = await client.post("/api/v1/settings/area-owners", json={
        "user_id": str(supervisor.id), "location_id": str(area)})
    assert ok.status_code in (200, 201), ok.text
    assert ok.json()["location_path"] == "Production/108 – Yard"
