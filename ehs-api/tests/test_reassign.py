"""★ Round 10 (q10-clear): "how does the hse role move items from a staff
member who has left the organization to a different staff member? … hse role
needs a way to review and reassign items." Rules agreed with uniops-8b."""
import uuid
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.action import Action
from app.models.communication import Communication
from app.models.mirrors import Task, User
from app.services import tasks as task_service
from tests.conftest import authed_client, make_token, make_user

DUE = (date.today() + timedelta(days=7)).isoformat()


async def _leave(test_engine, person):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(update(User).where(User.id == person.id).values(is_active=False))
        await db.commit()


async def _action(hse, owner, title="Guard the auger"):
    made = await hse.post("/api/v1/actions", json={
        "source_type": "manual", "title": title, "owner_id": str(owner.id), "due_date": DUE})
    assert made.status_code in (200, 201), made.text
    return made.json()


async def _open_tasks(db, doc_id):
    db.expire_all()
    return list((await db.execute(select(Task).where(
        Task.document_id == uuid.UUID(doc_id), Task.is_completed.is_(False)))).scalars())


async def test_preview_then_hand_over_with_the_tasks(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="requester", full_name="Lee Leaver")
    heir = await make_user(test_engine, role="requester", full_name="Hal Heir")
    a = await _action(hse, leaver)
    await _leave(test_engine, leaver)

    pre = (await hse.get(f"/api/v1/reassign/{leaver.id}")).json()
    assert pre["is_active"] is False
    [group] = [g for g in pre["groups"] if g["key"] == "action"]
    assert [i["ref"] for i in group["items"]] == [a["action_no"]]

    done = await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"action": str(heir.id)}, "reason": "Lee left on Friday"})
    assert done.status_code == 200, done.text
    assert done.json()["summary"]["moved"]["action"]["count"] == 1

    row = await db_session.get(Action, uuid.UUID(a["id"]))
    await db_session.refresh(row)
    assert row.owner_id == heir.id and row.owner_name == "Hal Heir"
    [task] = await _open_tasks(db_session, a["id"])
    assert task.assigned_user_id == heir.id and task.type == task_service.TASK_DO_ACTION
    # One email to the heir, naming the leaver and the reason.
    sent = list((await db_session.execute(select(Communication).where(
        Communication.template_key == "reassigned",
        Communication.to_addrs.contains([heir.email])))).scalars())
    assert len(sent) == 1 and "Lee Leaver" in sent[0].body_text and "Lee left on Friday" in sent[0].body_text
    # Nothing is left with the leaver.
    assert not (await hse.get(f"/api/v1/reassign/{leaver.id}")).json()["groups"]


async def test_all_or_nothing_and_every_problem_listed(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="requester")
    gone = await make_user(test_engine, role="requester", full_name="Also Gone")
    a = await _action(hse, leaver)
    await _leave(test_engine, gone)
    from app.models.asset import SafetyAsset
    asset = (await hse.post("/api/v1/safety-assets", json={"name": "Eyewash X"})).json()
    await db_session.execute(update(SafetyAsset).where(SafetyAsset.id == uuid.UUID(asset["id"]))
                             .values(assigned_user_id=leaver.id))
    await db_session.flush()
    worker = await make_user(test_engine, role="requester")  # may not inspect equipment

    bad = await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"action": str(gone.id), "asset": str(worker.id)}, "reason": "Left in October"})
    assert bad.status_code == 422, bad.text
    problems = bad.json()["detail"]["problems"]
    assert any("Also Gone has left too" in p for p in problems)
    assert any("ehs.asset.inspect" in p for p in problems)
    row = await db_session.get(Action, uuid.UUID(a["id"]))
    await db_session.refresh(row)
    assert row.owner_id == leaver.id, "nothing is written when anything fails"
    # And to the leaver themselves is refused too.
    assert (await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"action": str(leaver.id)}, "reason": "Left in October"})).status_code == 422


async def test_a_kind_may_be_left_where_it_is(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="requester")
    heir = await make_user(test_engine, role="requester")
    a = await _action(hse, leaver)
    done = await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"action": None}, "reason": "Deciding later"})
    assert done.status_code == 200
    row = await db_session.get(Action, uuid.UUID(a["id"]))
    await db_session.refresh(row)
    assert row.owner_id == leaver.id
    assert heir  # unused: nobody was named


async def test_their_own_tasks_close_without_reading_as_done(test_engine, db_session, hse_manager):
    """Read and sign cannot be anyone else's; closed, it must not read as signed."""
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="requester", full_name="Rae Reader")
    hse_user, _ = hse_manager
    doc_id = uuid.uuid4()
    t = await task_service.open_task(
        db_session, task_type=task_service.TASK_READ_SIGN, doc_type=task_service.DOC_DOCUMENT,
        doc_id=doc_id, doc_number="DOC-9", title="Read and sign: Lockout",
        assigned_role="requester", assigned_user_id=leaver.id)
    await db_session.flush()
    pre = (await hse.get(f"/api/v1/reassign/{leaver.id}")).json()
    assert pre["own_tasks"] == {task_service.TASK_READ_SIGN: 1}
    done = await hse.post(f"/api/v1/reassign/{leaver.id}", json={"to": {}, "reason": "Left in June"})
    assert done.json()["summary"]["own_tasks_closed"] == 1
    await db_session.refresh(t)
    assert t.is_completed and t.completed_by == hse_user.id, "closed by HSE, not by them"
    assert "Rae Reader has left. Not done by them." in t.description
    from app.models.document import DocumentAcknowledgement
    assert not (await db_session.execute(select(DocumentAcknowledgement).where(
        DocumentAcknowledgement.user_id == leaver.id))).first(), "no signature is made up"


async def test_a_recommendation_keeps_who_it_was_sent_to(test_engine, db_session, hse_manager):
    from datetime import date as _date
    _, hse = hse_manager
    leaver = await make_user(test_engine, role="requester", full_name="Jo Sent")
    heir = await make_user(test_engine, role="requester", full_name="Kim Next")
    meeting = (await hse.post("/api/v1/jhsc/meetings", json={
        "held_on": _date.today().isoformat(), "title": "Monthly"})).json()
    rec = (await hse.post(f"/api/v1/jhsc/meetings/{meeting['id']}/recommendations", json={
        "recommendation": "Replace the eyewash station", "send_to": [str(leaver.id)]})).json()
    done = await hse.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {"jhsc": str(heir.id)}, "reason": "Jo has left"})
    assert done.status_code == 200, done.text
    after = (await hse.get(f"/api/v1/jhsc/recommendations/{rec['id']}")).json()
    names = [p["name"] for p in after["sent_to"]]
    assert names == ["Jo Sent", "Kim Next"], "the record of who it was sent to is kept"
    tasks = [t for t in await _open_tasks(db_session, rec["id"])
             if t.type == task_service.TASK_JHSC_REPLY]
    assert [t.assigned_user_id for t in tasks] == [heir.id]
    filed = list((await db_session.execute(select(Communication).where(
        Communication.doc_id == uuid.UUID(rec["id"]),
        Communication.template_key == "jhsc_reassigned"))).scalars())
    assert len(filed) == 1 and "Replace the eyewash station" in filed[0].body_text
    assert "due" in filed[0].body_text


async def test_only_hse(test_engine, db_session, worker):
    _, someone = worker
    leaver = await make_user(test_engine, role="requester")
    assert (await someone.get(f"/api/v1/reassign/{leaver.id}")).status_code == 403
    assert (await someone.post(f"/api/v1/reassign/{leaver.id}", json={
        "to": {}, "reason": "Not mine to do"})).status_code == 403
