"""★ Round 12, batch 5: the new-item flags.

ACT-01 news, ACT-02/03 new work (and the "New" tile), ACT-04 an answered
comment, INC-08 new incidents for HSE and the area's supervisors. Seen is per
person, and nothing older than a person's baseline is new.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.models.mirrors import Task
from app.models.seen import SeenBaseline
from app.services.day_range import plant_today
from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_anonymity_and_edit import _report


async def _baseline(db, user, ago=timedelta(hours=1)):
    """Where "new" starts, set by hand: the test runs in one transaction, so
    the database clock does not move between the steps of a test."""
    db.add(SeenBaseline(user_id=user.id, since=datetime.now(timezone.utc) - ago,
                        news_seen_at=datetime.now(timezone.utc) - ago))
    await db.flush()


def _action(owner, **kw):
    return {"source_type": "inspection", "source_id": str(uuid.uuid4()), "title": "Fit a guard",
            "hierarchy_of_control": "engineering", "owner_id": str(owner.id),
            "due_date": (plant_today() + timedelta(days=7)).isoformat(), **kw}


async def test_new_work_is_flagged_until_it_is_opened(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    owner = await make_user(test_engine, role="requester")
    await _baseline(db_session, owner)
    old = (await hse.post("/api/v1/actions", json=_action(owner, title="Old one"))).json()
    await db_session.execute(update(Task).where(Task.document_id == uuid.UUID(old["id"]))
                             .values(created_at=datetime.now(timezone.utc) - timedelta(days=2)))
    new = (await hse.post("/api/v1/actions", json=_action(owner))).json()
    await db_session.flush()

    async with authed_client(make_token(owner.id, owner.role), db_session) as me:
        got = (await me.get("/api/v1/seen")).json()
        assert got["action_ids"] == [new["id"]], "★ older than the baseline is not new"
        assert got["my_actions"] == 1 and len(got["task_ids"]) == 1

        assert (await me.post("/api/v1/seen/opened", json={
            "doc_type": "ehs_act", "doc_id": new["id"]})).status_code == 204
        got = (await me.get("/api/v1/seen")).json()
        assert got["action_ids"] == [] and got["my_actions"] == 0, "opening it clears it"


async def test_work_handed_over_is_new_to_whoever_receives_it(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    first = await make_user(test_engine, role="requester")
    second = await make_user(test_engine, role="requester")
    for u in (first, second):
        await _baseline(db_session, u)
    made = (await hse.post("/api/v1/actions", json=_action(first))).json()
    async with authed_client(make_token(first.id, first.role), db_session) as c:
        await c.post("/api/v1/seen/opened", json={"doc_type": "ehs_act", "doc_id": made["id"]})
    moved = await hse.post(f"/api/v1/actions/{made['id']}/reassign",
                           json={"owner_id": str(second.id), "reason": "Wrong person"})
    assert moved.status_code == 200, moved.text
    await db_session.flush()
    async with authed_client(make_token(second.id, second.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["action_ids"] == [made["id"]]
    async with authed_client(make_token(first.id, first.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["my_actions"] == 0


async def test_a_card_pressed_on_my_actions_counts_and_not_somebody_elses(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    owner = await make_user(test_engine, role="requester")
    other = await make_user(test_engine, role="requester")
    for u in (owner, other):
        await _baseline(db_session, u)
    await hse.post("/api/v1/actions", json=_action(owner))
    await db_session.flush()
    async with authed_client(make_token(owner.id, owner.role), db_session) as c:
        task_id = (await c.get("/api/v1/seen")).json()["task_ids"][0]
    async with authed_client(make_token(other.id, other.role), db_session) as c:
        await c.post("/api/v1/seen/task", json={"task_id": task_id})
    async with authed_client(make_token(owner.id, owner.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["task_ids"] == [task_id], \
            "somebody else pressing it does not clear mine"
        await c.post("/api/v1/seen/task", json={"task_id": task_id})
        assert (await c.get("/api/v1/seen")).json()["task_ids"] == []


async def test_news_is_new_until_my_actions_is_opened(test_engine, db_session, hse_manager):
    hse_user, hse = hse_manager
    reader = await make_user(test_engine, role="requester")
    await _baseline(db_session, reader)
    await _baseline(db_session, hse_user)
    assert (await hse.post("/api/v1/news", json={"importance": "high",
                                                  "message": "Forklift lane closed"})).status_code == 201
    assert (await hse.get("/api/v1/seen")).json()["news"] == 0, "not new to whoever posted it"
    async with authed_client(make_token(reader.id, reader.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["news"] == 1
        await c.post("/api/v1/seen/news")
        assert (await c.get("/api/v1/seen")).json()["news"] == 0


async def _new(c) -> list[str]:
    page = await c.get("/api/v1/seen/incidents")
    assert page.status_code == 200, page.text
    return [i["id"] for i in page.json()["items"]]


async def test_a_new_incident_is_flagged_for_hse_and_not_for_the_reporter(
        test_engine, db_session, hse_manager):
    hse_user, hse = hse_manager
    await _baseline(db_session, hse_user)
    reporter, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    got = (await hse.get("/api/v1/seen")).json()
    assert got["incidents"] == 1 and await _new(hse) == [iid], "near misses included (D12)"
    assert got["my_actions"] >= 1

    async with authed_client(make_token(reporter.id, reporter.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["incidents"] == 0, "not one's own report"
    supervisor = await make_user(test_engine, role="area_supervisor")
    await _baseline(db_session, supervisor)
    async with authed_client(make_token(supervisor.id, supervisor.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["incidents"] == 0, \
            "a supervisor down for no area is not told about every incident"

    await hse.post("/api/v1/seen/opened", json={"doc_type": "ehs_inc", "doc_id": iid})
    assert (await hse.get("/api/v1/seen")).json()["incidents"] == 0
    assert await _new(hse) == []


async def test_an_area_owner_sees_their_area_and_only_if_they_may_read_incidents(
        test_engine, db_session):
    """★ B5 review B1 and B6: the area is matched in the query, and an area
    owner who may not read the register (any role may be one) is not shown
    incident titles they could not open."""
    import uuid as _uuid

    from sqlalchemy import text as sql_text

    from app.models.area_owner import AreaOwner
    here, there = _uuid.uuid4(), _uuid.uuid4()
    for loc_id, path in ((here, "Production/103 – Filling"), (there, "Production/105 – Warehouse")):
        await db_session.execute(sql_text(
            "insert into locations (id, code, name, level, path, depth, is_active)"
            " values (:id, :code, :path, 'department', :path, 1, true)"),
            {"id": loc_id, "code": str(loc_id)[:8], "path": path})
    supervisor = await make_user(test_engine, role="area_supervisor")
    worker = await make_user(test_engine, role="requester")
    for u in (supervisor, worker):
        db_session.add(AreaOwner(id=_uuid.uuid4(), user_id=u.id, user_name=u.full_name,
                                 location_id=here, location_path="Production/103 – Filling"))
        await _baseline(db_session, u)
    await db_session.flush()
    _, mine = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    _, theirs = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
    await db_session.execute(sql_text("update ehs_incidents set location_id = :l where id = :i"),
                             {"l": here, "i": mine})
    await db_session.execute(sql_text("update ehs_incidents set location_id = :l where id = :i"),
                             {"l": there, "i": theirs})
    await db_session.flush()

    async with authed_client(make_token(supervisor.id, supervisor.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["incidents"] == 1
        assert await _new(c) == [mine], "their own area's, not the warehouse's"
        assert (await c.get("/api/v1/seen/incidents", params={"sort": "nope"})).status_code == 422
    async with authed_client(make_token(worker.id, worker.role), db_session) as c:
        assert (await c.get("/api/v1/seen")).json()["incidents"] == 0, \
            "★ an area owner who cannot read the register is not shown its titles"
        assert await _new(c) == []


async def test_an_answered_comment_is_flagged_for_whoever_left_it(
        test_engine, db_session, hse_manager):
    _, hse = hse_manager
    author = await make_user(test_engine, role="requester")
    await _baseline(db_session, author)
    async with authed_client(make_token(author.id, author.role), db_session) as c:
        left = (await c.post("/api/v1/feedback", json={"body": "The eyewash form is long"})).json()
        assert (await c.get("/api/v1/seen")).json()["feedback_answered"] == 0
        assert (await hse.patch(f"/api/v1/feedback/{left['id']}",
                                json={"status": "done", "reply": "Shortened."})).status_code == 200
        assert (await c.get("/api/v1/seen")).json()["feedback_answered"] == 1
        await c.post("/api/v1/seen/feedback")
        assert (await c.get("/api/v1/seen")).json()["feedback_answered"] == 0
        from app.models.seen import SeenMark
        await db_session.execute(update(SeenMark).values(
            seen_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        assert (await hse.patch(f"/api/v1/feedback/{left['id']}",
                                json={"status": "done", "reply": "Shortened again."})).status_code == 200
        assert (await c.get("/api/v1/seen")).json()["feedback_answered"] == 1, \
            "a second answer after the first was read is new again"


async def test_a_new_starters_first_work_is_new(test_engine, db_session, hse_manager):
    """★ B5 review B4: the baseline is when the flags went live for those who
    were here, and when the account was made for those who joined since."""
    hse_user, hse = hse_manager
    await _baseline(db_session, hse_user, ago=timedelta(days=30))   # the flags went live
    starter = await make_user(test_engine, role="requester")       # joined since
    await hse.post("/api/v1/actions", json=_action(starter))         # before they ever looked
    # The test's one transaction began before the account was made; in use the
    # work is handed over after it.
    await db_session.execute(update(Task).where(Task.assigned_user_id == starter.id)
                             .values(created_at=datetime.now(timezone.utc)))
    await db_session.flush()
    async with authed_client(make_token(starter.id, starter.role), db_session) as c:
        assert len((await c.get("/api/v1/seen")).json()["action_ids"]) == 1


async def test_a_role_task_opened_from_the_portal_is_opened(test_engine, db_session):
    """★ B5 review B5: a task given to a role, opened from the Portal's inbox."""
    from app.services import tasks as task_service
    hse_user = await make_user(test_engine, role="ehs_manager")
    await _baseline(db_session, hse_user)
    task = await task_service.open_task(
        db_session, task_type=task_service.TASK_REVIEW_CAPAS, doc_type=task_service.DOC_INCIDENT,
        doc_id=uuid.uuid4(), doc_number="INC-X", title="Review it", assigned_role="ehs_manager",
        assigned_user_id=None, description=None, priority="normal")
    await db_session.flush()
    async with authed_client(make_token(hse_user.id, hse_user.role), db_session) as c:
        assert str(task.id) in (await c.get("/api/v1/seen")).json()["task_ids"]
        assert (await c.get(f"/api/v1/actions/tasks/{task.id}/open")).status_code == 200
        assert str(task.id) not in (await c.get("/api/v1/seen")).json()["task_ids"]
