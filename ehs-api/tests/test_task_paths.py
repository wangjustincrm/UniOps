"""Where a Safety task opens — in Safety's My Actions and from the Portal.

Round 7, TC-03 and TC-05: "permission denied when a worker who is assigned to
run a course session tries to open the course session." Their task opened the
training register, which an ordinary employee may not read.
"""
import uuid

from app.services import tasks as task_service


async def _task(db, *, task_type, doc_type, doc_id, user_id):
    task = await task_service.open_task(
        db, task_type=task_type, doc_type=doc_type, doc_id=doc_id, doc_number="CRS-1",
        title="A task", assigned_role=task_service.ROLE_ANY_EMPLOYEE, assigned_user_id=user_id)
    await db.commit()
    return task


async def test_running_a_session_opens_the_session(worker, db_session):
    me, client = worker
    session_id = uuid.uuid4()
    task = await _task(db_session, task_type=task_service.TASK_COURSE_ADMIN,
                       doc_type="ehs_crs", doc_id=session_id, user_id=me.id)
    mine = (await client.get("/api/v1/actions/mine/tasks")).json()
    assert next(t for t in mine if t["id"] == str(task.id))["path"] == f"/training/sessions/{session_id}"
    opened = await client.get(f"/api/v1/actions/tasks/{task.id}/open")
    assert opened.status_code == 200 and opened.json()["path"] == f"/training/sessions/{session_id}"


async def test_attending_a_session_opens_my_training(worker, db_session):
    me, client = worker
    task = await _task(db_session, task_type=task_service.TASK_COURSE_ATTEND,
                       doc_type="ehs_crs", doc_id=uuid.uuid4(), user_id=me.id)
    assert (await client.get(f"/api/v1/actions/tasks/{task.id}/open")).json()["path"] == "/training/mine"


async def test_other_tasks_still_open_by_document(worker, db_session):
    """The admission: an incident's task opens the incident, as before."""
    me, client = worker
    iid = uuid.uuid4()
    task = await _task(db_session, task_type="ehs_investigate", doc_type="ehs_inc",
                       doc_id=iid, user_id=me.id)
    assert (await client.get(f"/api/v1/actions/tasks/{task.id}/open")).json()["path"] == f"/incidents/{iid}"


async def test_a_task_that_is_not_safetys_is_not_answered(worker, db_session):
    me, client = worker
    task = await _task(db_session, task_type="ehs_x", doc_type="po", doc_id=uuid.uuid4(), user_id=me.id)
    assert (await client.get(f"/api/v1/actions/tasks/{task.id}/open")).status_code == 404
