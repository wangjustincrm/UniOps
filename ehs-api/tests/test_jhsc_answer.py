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
