"""Round 9: a JHSC recommendation opened and answered by whoever it was sent to.

"when a worker or a supervisor opens a JHSC recommendation assigned to them from
the my actions register, open the recommendation directly." "when a worker, who
has a jhsc recommendation assigned to them, they can not open the recommendation
and fill in what was completed. give them a root cause corrective action form …
once it is submitted, remove it from the workers my actions registry. allow the
HSE role edit rights after this is submitted." "create a 'who is told' for the
JHSC recommendation when a root cause corrective action form is completed. log
this communication on the jhsc recommendation."
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.communication import Communication
from app.models.config import EhsConfig
from app.models.mirrors import Task
from app.services import tasks as task_service
from tests.conftest import authed_client, make_token, make_user
from app.services.day_range import plant_today


async def _sent(hse, to) -> tuple[dict, dict]:
    meeting = (await hse.post("/api/v1/jhsc/meetings", json={
        "held_on": plant_today().isoformat(), "title": "Monthly meeting"})).json()
    rec = await hse.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
        "recommendation": "Guard the auger", "send_to": [str(u.id) for u in to]})
    assert rec.status_code == 201, rec.text
    return meeting, rec.json()


def _answer(owner, **kw):
    return {"title": "Fit an interlocked guard", "description": "Order from the OEM",
            "owner_id": str(owner.id), "due_date": (plant_today() + timedelta(days=14)).isoformat(),
            "priority": "high", **kw}


@pytest.fixture
async def asked(test_engine, db_session):
    """An ordinary employee the recommendation is sent to, and their client."""
    user = await make_user(test_engine, role="requester", full_name="Asked Person")
    async with authed_client(make_token(user.id, user.role), db_session) as c:
        yield user, c


async def test_the_person_asked_opens_it_and_nobody_else_does(hse_manager, asked, worker):
    _, hse = hse_manager
    person, client = asked
    _, other = worker
    _, rec = await _sent(hse, [person])

    mine = await client.get(f"/api/v1/jhsc/recommendations/{rec['id']}")
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["recommendation"] == "Guard the auger"
    assert body["may_answer"] is True and body["may_manage"] is False
    assert body["reply_due_at"] and body["meeting_no"]

    assert (await other.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).status_code == 403


async def test_the_task_opens_the_recommendation(hse_manager, asked, db_session):
    _, hse = hse_manager
    person, _ = asked
    _, rec = await _sent(hse, [person])
    [task] = (await db_session.execute(select(Task).where(
        Task.assigned_user_id == person.id, Task.type == task_service.TASK_JHSC_REPLY,
        Task.is_completed.is_(False)))).scalars().all()
    assert task_service.path_for(task) == f"/jhsc/recommendations/{rec['id']}"


async def test_answering_raises_the_action_and_clears_the_task(hse_manager, asked, db_session):
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])

    got = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                            json=_answer(person))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["outcome"] == "accepted" and body["responded_by_name"] == "Asked Person"
    assert body["action"]["title"] == "Fit an interlocked guard"
    assert body["action"]["priority"] == "high" and body["action"]["owner_name"] == "Asked Person"
    assert body["may_answer"] is False

    db_session.expire_all()
    open_reply = (await db_session.execute(select(Task).where(
        Task.assigned_user_id == person.id, Task.type == task_service.TASK_JHSC_REPLY,
        Task.is_completed.is_(False)))).scalars().all()
    assert open_reply == [], "the answered recommendation leaves their My Actions"
    # …and the action they committed to is now there instead.
    assert (await db_session.execute(select(Task).where(
        Task.assigned_user_id == person.id, Task.type == task_service.TASK_DO_ACTION,
        Task.is_completed.is_(False)))).scalars().first() is not None

    again = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                              json=_answer(person))
    assert again.status_code == 409


async def test_only_the_people_asked_or_hse_answer(hse_manager, asked, worker):
    _, hse = hse_manager
    person, _ = asked
    _, other = worker
    _, rec = await _sent(hse, [person])
    refused = await other.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                               json=_answer(person))
    assert refused.status_code == 403
    assert (await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                           json=_answer(person))).status_code == 200


async def test_who_is_told_and_it_is_filed_on_the_recommendation(
        hse_manager, asked, test_engine, db_session):
    hse_user, hse = hse_manager
    person, client = asked
    listed = await make_user(test_engine, role="requester", full_name="On The List")
    cfg = await db_session.get(EhsConfig, 1)
    if cfg is None:
        cfg = EhsConfig(id=1)
        db_session.add(cfg)
    cfg.jhsc_answered_notify = {"people": [str(listed.id)], "sender": True}
    await db_session.commit()

    _, rec = await _sent(hse, [person])
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    db_session.expire_all()
    [mail] = (await db_session.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(rec["id"]),
        Communication.template_key == "jhsc_recommendation_answered"))).scalars().all()
    assert sorted(mail.to_addrs) == sorted([listed.email, hse_user.email])
    assert "Fit an interlocked guard" in mail.body_text and "Guard the auger" in mail.body_text

    # Listed on the recommendation, through the ordinary correspondence log.
    log = await hse.get(f"/api/v1/communications?doc_type=ehs_jhsc&doc_id={rec['id']}")
    assert log.status_code == 200
    assert [c["template_key"] for c in log.json()] == ["jhsc_recommendation_answered"]


async def test_the_sender_can_be_left_off(hse_manager, asked, test_engine, db_session):
    hse_user, hse = hse_manager
    person, client = asked
    listed = await make_user(test_engine, role="requester")
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()
    saved = await hse.put("/api/v1/settings/config", json={
        "jhsc_answered_notify": {"people": [str(listed.id)], "sender": False}})
    assert saved.status_code == 200, saved.text
    _, rec = await _sent(hse, [person])
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    db_session.expire_all()
    [mail] = (await db_session.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(rec["id"]),
        Communication.template_key == "jhsc_recommendation_answered"))).scalars().all()
    assert mail.to_addrs == [listed.email]


async def test_hse_corrects_the_answer_afterwards(hse_manager, asked):
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    early = await hse.patch(f"/api/v1/jhsc/recommendations/{rec['id']}/response",
                            json={"response": "x"})
    assert early.status_code == 409
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    assert (await client.patch(f"/api/v1/jhsc/recommendations/{rec['id']}/response",
                               json={"response": "mine"})).status_code == 403
    fixed = await hse.patch(f"/api/v1/jhsc/recommendations/{rec['id']}/response",
                            json={"response": "Interlocked guard, ordered 23 Oct", "outcome": "deferred"})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["response"] == "Interlocked guard, ordered 23 Oct"
    assert fixed.json()["outcome"] == "deferred"


async def test_the_list_says_when_each_reply_is_due(hse_manager, asked):
    """For the figures above the list: answered on time, late, still open."""
    _, hse = hse_manager
    person, _ = asked
    _, rec = await _sent(hse, [person])
    listed = (await hse.get("/api/v1/jhsc/recommendations")).json()
    [mine] = [r for r in listed if r["id"] == rec["id"]]
    assert mine["reply_due_at"]


async def test_the_list_carries_the_state_of_each_corrective_action(hse_manager, asked, db_session):
    """★ Round 10, TC-10: CAPA figures on the recommendations tab — raised,
    completed, outstanding — counted from what the list carries."""
    import uuid as _uuid
    from app.models.action import Action

    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    answered = (await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                                  json=_answer(person))).json()
    listed = {r["id"]: r for r in (await hse.get("/api/v1/jhsc/recommendations")).json()}
    assert listed[rec["id"]]["action_status"] == "open"
    row = await db_session.get(Action, _uuid.UUID(answered["action"]["id"]))
    row.status = "closed"
    await db_session.commit()
    listed = {r["id"]: r for r in (await hse.get("/api/v1/jhsc/recommendations")).json()}
    assert listed[rec["id"]]["action_status"] == "closed"


@pytest.fixture
async def states(db_session):
    """The four states, as the migration seeds them (the test schema is built
    from the models, not the migrations)."""
    from app.models.vocabulary import Vocabulary, VocabularyItem
    if await db_session.get(Vocabulary, "jhsc_rec_status") is None:
        db_session.add(Vocabulary(code="jhsc_rec_status", name="JHSC recommendation status"))
        await db_session.flush()
    items = []
    for sort, (code, label) in enumerate([("new_request", "New request"),
                                          ("maintenance_review", "Under review by maintenance"),
                                          ("in_progress", "Work in progress"),
                                          ("hse_verification", "Awaiting HSE verification"),
                                          ("completed", "Completed")], start=1):
        item = VocabularyItem(id=uuid.uuid4(), vocabulary_code="jhsc_rec_status", code=f"{code}-{uuid.uuid4().hex[:4]}",
                              label=label, sort_order=sort, is_active=True, attrs={}, path=label)
        db_session.add(item)
        items.append(item)
    await db_session.flush()
    return items


async def test_a_recommendation_has_a_state_from_hses_list(hse_manager, asked, worker, states, db_session):
    """★ Round 10, TC-11: "add jhsc task list in the settings/lists registry for
    picking the current state of recommendations. add 4 states." """
    from app.models.vocabulary import VocabularyItem
    _, hse = hse_manager
    person, client = asked
    _, other = worker
    _, rec = await _sent(hse, [person])
    assert rec["status_label"] == "New request", "starts at the first state on the list"

    moved = await client.put(f"/api/v1/jhsc/recommendations/{rec['id']}/status",
                             json={"item_id": str(states[1].id)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["status_label"] == "Under review by maintenance"
    assert moved.json()["may_set_status"] is True

    assert (await other.put(f"/api/v1/jhsc/recommendations/{rec['id']}/status",
                            json={"item_id": str(states[2].id)})).status_code == 403
    assert (await hse.put(f"/api/v1/jhsc/recommendations/{rec['id']}/status",
                          json={"item_id": str(states[2].id)})).status_code == 200

    # Something from another list is refused.
    from app.models.vocabulary import Vocabulary
    if await db_session.get(Vocabulary, "body_part") is None:
        db_session.add(Vocabulary(code="body_part", name="Body part"))
        await db_session.flush()
    stranger = VocabularyItem(id=uuid.uuid4(), vocabulary_code="body_part", code=f"x-{uuid.uuid4().hex[:4]}",
                              label="Knee", sort_order=1, is_active=True, attrs={}, path="Knee")
    db_session.add(stranger)
    await db_session.flush()
    assert (await hse.put(f"/api/v1/jhsc/recommendations/{rec['id']}/status",
                          json={"item_id": str(stranger.id)})).status_code == 422

    listed = {r["id"]: r for r in (await hse.get("/api/v1/jhsc/recommendations")).json()}
    assert listed[rec["id"]]["status_label"] == "Work in progress"


async def test_answering_locks_the_recommendation_first(hse_manager, asked, test_engine):
    """★ Review (uniops-8b): two recipients answering together both passed the
    "already answered" check. The row is locked before it, so the second waits
    for the first and is refused. (Two real transactions cannot be run inside
    the test's one; this checks the lock is taken.)"""
    from sqlalchemy import event
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    seen: list[str] = []

    def grab(conn, cursor, statement, params, context, executemany):
        seen.append(statement)
    event.listen(test_engine.sync_engine, "before_cursor_execute", grab)
    try:
        got = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                                json=_answer(person))
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", grab)
    assert got.status_code == 200, got.text
    assert any("ehs_jhsc_recommendations" in s and "FOR UPDATE" in s for s in seen)


async def test_a_new_recommendation_starts_at_new_request_wherever_it_sorts(
        hse_manager, asked, states, db_session):
    """★ HSE added "Waiting on Parts" and it took the first place on the list;
    every new recommendation then opened in it. Round 10 asked for New request."""
    from app.models.vocabulary import VocabularyItem
    states[0].code = "new_request"
    db_session.add(VocabularyItem(
        id=uuid.uuid4(), vocabulary_code="jhsc_rec_status", code=f"parts-{uuid.uuid4().hex[:4]}",
        label="Waiting on Parts", sort_order=0, is_active=True, attrs={}, path="Waiting on Parts"))
    await db_session.flush()
    _, hse = hse_manager
    person, _ = asked
    _, rec = await _sent(hse, [person])
    assert rec["status_label"] == "New request"


def _codes(states):
    for item, code in zip(states, ("new_request", "maintenance_review", "in_progress",
                                   "hse_verification", "completed")):
        item.code = code


async def _state(client, rec_id):
    return (await client.get(f"/api/v1/jhsc/recommendations/{rec_id}")).json()["status_label"]


async def test_the_state_follows_the_reply_to_completed(hse_manager, asked, states, db_session):
    """★ Review: accepted is being worked on until its action is closed."""
    _codes(states)
    await db_session.flush()
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    answered = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                                 json=_answer(person))
    assert answered.status_code == 200, answered.text
    assert await _state(hse, rec["id"]) == "Work in progress"

    action_id = answered.json()["action"]["id"]
    from app.models.action import Action
    action = await db_session.get(Action, uuid.UUID(action_id))
    action.status = "pending_verification"
    await db_session.flush()
    verified = await hse.post(f"/api/v1/actions/{action_id}/verify",
                              json={"is_effective": True, "notes": "Guard fitted and checked."})
    assert verified.status_code == 200, verified.text
    # ★ Round 12: the supervisor's verification hands it to HSE (step 10) …
    assert await _state(hse, rec["id"]) == "Awaiting HSE verification"
    from sqlalchemy import select as _select
    from app.models.mirrors import Task
    db_session.expire_all()
    review = (await db_session.execute(_select(Task).where(
        Task.type == "ehs_review_capas", Task.document_id == uuid.UUID(rec["id"])))).scalar_one()
    review_id = review.id
    ticked = await hse.post(f"/api/v1/actions/tasks/{review_id}/reviewed")
    assert ticked.status_code == 409, \
        "★ review L3: ticked off in My Actions, it was left awaiting HSE with no reminder"
    assert (await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/verify")).status_code == 403
    # … and HSE verifying it in the register completes it (step 11).
    done = await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/verify")
    assert done.status_code == 200, done.text
    assert done.json()["status_label"] == "Completed" and done.json()["hse_verified_by_name"]
    db_session.expire_all()
    assert (await db_session.get(Task, review_id)).is_completed, "verifying it is the review"
    again = await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/verify")
    assert again.status_code == 409, "★ review L4: a second call overwrote who verified it"
    got = (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    assert got["may_hse_verify"] is False


async def test_completed_before_step_11_is_not_offered_again(hse_manager, asked, states, db_session):
    """★ Review L4: recommendations already at Completed when 0092 added HSE's
    verification had no verifier, and the card came back on them."""
    _codes(states)
    await db_session.flush()
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    from app.models.jhsc import JhscRecommendation
    from app.models.action import Action
    row = await db_session.get(JhscRecommendation, uuid.UUID(rec["id"]))
    if row.action_id:
        (await db_session.get(Action, row.action_id)).status = "closed"
    from app.models.vocabulary import VocabularyItem
    from sqlalchemy import select as _select
    item = (await db_session.execute(_select(VocabularyItem).where(
        VocabularyItem.vocabulary_code == "jhsc_rec_status",
        VocabularyItem.code == "completed"))).scalars().first()
    row.status_item_id, row.status_label = item.id, item.label
    await db_session.commit()
    got = (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    assert got["may_hse_verify"] is True, \
        "★ re-review N1: the state alone is not HSE's verification"
    row.completed_before_verification = True     # what 0097 marks
    await db_session.commit()
    got = (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    assert got["may_hse_verify"] is False
    assert (await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/verify")).status_code == 409


async def test_completed_is_picked_only_by_whoever_verifies(hse_manager, asked, states, db_session):
    """★ Re-review N1: the person it was sent to could pick Completed in the
    state list, skipping HSE's verification and leaving its review task open
    for good. Only HSE picks it, and picking it is verifying it."""
    _codes(states)
    await db_session.flush()
    codes = {i.code: i.id for i in states}
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    url = f"/api/v1/jhsc/recommendations/{rec['id']}/status"
    detail = (await client.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    assert detail["may_complete"] is False
    assert (await client.put(url, json={"item_id": str(codes["in_progress"])})).status_code == 200, \
        "the person it was sent to still sets the other states"
    refused = await client.put(url, json={"item_id": str(codes["completed"])})
    assert refused.status_code == 403

    from sqlalchemy import select as _select
    from app.models.mirrors import Task
    from app.services import tasks as task_service
    task = await task_service.open_task(
        db_session, task_type="ehs_review_capas", doc_type="ehs_jhsc",
        doc_id=uuid.UUID(rec["id"]), doc_number="X", title="Review", assigned_role="ehs_manager")
    task_id = task.id
    await db_session.commit()
    assert (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()["may_complete"]
    early = await hse.put(url, json={"item_id": str(codes["completed"])})
    assert early.status_code == 409, \
        "★ re-review N7: not answered yet, so not verified — as the Verify button says"

    answered = await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer",
                                 json=_answer(person))
    assert answered.status_code == 200, answered.text
    from app.models.action import Action
    action_id = uuid.UUID(answered.json()["action"]["id"])
    still_open = await hse.put(url, json={"item_id": str(codes["completed"])})
    assert still_open.status_code == 409, "its corrective action is still open"
    (await db_session.get(Action, action_id)).status = "closed"
    await db_session.commit()
    done = await hse.put(url, json={"item_id": str(codes["completed"])})
    assert done.status_code == 200, done.text
    assert done.json()["hse_verified_by_name"], "HSE picking Completed is verifying it"
    db_session.expire_all()
    assert (await db_session.get(Task, task_id)).is_completed, "and closes its review"


async def test_declined_stays_where_it_is(hse_manager, asked, states, db_session):
    _codes(states)
    await db_session.flush()
    _, hse = hse_manager
    person, _ = asked
    _, rec = await _sent(hse, [person])
    done = await hse.post(f"/api/v1/jhsc/recommendations/{rec['id']}/respond",
                          json={"response": "Not this year.", "outcome": "declined"})
    assert done.status_code == 200, done.text
    assert await _state(hse, rec["id"]) == "New request"


async def test_a_reply_does_not_undo_a_state_a_person_chose(hse_manager, asked, states, db_session):
    _codes(states)
    await db_session.flush()
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    from app.models.vocabulary import VocabularyItem
    parts = VocabularyItem(id=uuid.uuid4(), vocabulary_code="jhsc_rec_status",
                           code=f"parts-{uuid.uuid4().hex[:4]}", label="Waiting on Parts",
                           sort_order=9, is_active=True, attrs={}, path="Waiting on Parts")
    db_session.add(parts)
    await db_session.flush()
    chosen = await hse.put(f"/api/v1/jhsc/recommendations/{rec['id']}/status",
                           json={"item_id": str(parts.id)})
    assert chosen.status_code == 200, chosen.text
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    assert await _state(hse, rec["id"]) == "Waiting on Parts"


async def test_the_backfill_moves_only_what_0085_wrote(hse_manager, asked, states, db_session):
    """★ 0086 changed nothing and printed 0: with no action the join gives NULL,
    and the rows it was for fell out. So the row with no action is the case."""
    import importlib.util
    import pathlib
    from sqlalchemy import delete, text
    from app.models.action import Action
    from app.models.change_log import ChangeLogEntry as ChangeLog
    from app.models.jhsc import JhscRecommendation

    _codes(states)
    done = states[-1]  # completed
    _, hse = hse_manager
    person, _ = asked
    made = []
    for _i in range(4):
        _, rec = await _sent(hse, [person])
        made.append(rec["id"])
    no_action, with_closed, chosen, declined = [
        await db_session.get(JhscRecommendation, uuid.UUID(i)) for i in made]
    closed = (await db_session.execute(select(Action).limit(1))).scalars().first()
    if closed is None:
        closed = await db_session.get(Action, uuid.UUID((await hse.post("/api/v1/actions", json={
            "source_type": "manual", "title": "Guard it", "owner_id": str(person.id),
            "due_date": (date.today() + timedelta(days=7)).isoformat()})).json()["id"]))
    closed.status = "closed"
    for rec in (no_action, with_closed, chosen, declined):
        rec.outcome, rec.status_item_id, rec.status_label = "accepted", done.id, done.label
    with_closed.action_id = closed.id
    declined.outcome = "declined"
    await db_session.flush()
    await db_session.execute(delete(ChangeLog).where(
        ChangeLog.table_name == "ehs_jhsc_recommendations",
        ChangeLog.row_id.in_(made), ChangeLog.field == "status_item_id"))
    db_session.add(ChangeLog(id=uuid.uuid4(), table_name="ehs_jhsc_recommendations",
                             row_id=str(chosen.id), action="update", field="status_item_id",
                             new_value=str(done.id)))
    await db_session.flush()

    path = next(pathlib.Path("alembic/versions").glob("*_0087_*.py"))
    spec = importlib.util.spec_from_file_location("m0087", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    moved = await db_session.execute(text(migration.STATE_FROM_REPLY))
    assert moved.rowcount == 2, "the row with no action, and the declined one"

    for rec in (no_action, with_closed, chosen, declined):
        await db_session.refresh(rec)
    assert no_action.status_label == "Work in progress"
    assert declined.status_label == "New request"
    assert with_closed.status_label == "Completed", "accepted with its action closed is done"
    assert chosen.status_label == "Completed", "a state a person chose stays"



async def test_opening_it_puts_it_under_review(hse_manager, asked, worker, states, db_session):
    """★ Round 12, step 4: "When worker opens the request to read what is
    required, the status should automatically be changed to Under Review."
    Only somebody it was sent to; HSE reading it does not count."""
    _codes(states)
    await db_session.flush()
    _, hse = hse_manager
    person, client = asked
    _, rec = await _sent(hse, [person])
    assert await _state(hse, rec["id"]) == "New request", "HSE opening it is not the review"
    seen = (await client.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    assert seen["status_label"] == "Under review by maintenance"
    assert seen["reviewed_by_name"] == "Asked Person"
    await client.post(f"/api/v1/jhsc/recommendations/{rec['id']}/answer", json=_answer(person))
    assert await _state(hse, rec["id"]) == "Work in progress", "step 6"
    history = (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}/history")).json()
    whats = [h["what"] for h in history]
    assert whats[0] == "Recommended" and "Opened — under review" in whats
    assert any(w.startswith("Replied") for w in whats)
    assert any(w.startswith("Corrective action") and w.endswith("raised") for w in whats)
