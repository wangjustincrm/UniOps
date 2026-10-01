"""Whoever holds a task on a visit can open it (VMS PRD V2.7 §12 D-06 / D-10).

The Janitor's "Prepare PPE" task and a department manager's approval task
both deep-link to the visit page, but visibility only knew creator / Host /
QM / dept-of-Host — so those links showed "Visit not found".
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.task_mirror import Task
import app.db.session as session_module
from tests.conftest import authed_client, make_token, make_user


async def _visit(client, host_id) -> str:
    v = await client.post("/api/v1/visitors", json={
        "first_name": "Vis", "last_name": f"Ible{uuid.uuid4().hex[:4]}",
        "company_name": "VisCo", "visitor_type": "supplier",
    })
    arrival = datetime.now(timezone.utc) + timedelta(days=1)
    r = await client.post("/api/v1/visits", json={
        "visitor_id": v.json()["id"], "host_id": str(host_id),
        "visit_date": arrival.date().isoformat(), "planned_arrival": arrival.isoformat(),
        "visit_purpose": "meeting", "access_area": "office",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _task(visit_id, user, task_type="prepare_ppe", completed=False):
    async with session_module.AsyncSessionLocal() as db:
        db.add(Task(type=task_type, document_type="vms_visit", document_id=uuid.UUID(visit_id),
                    document_number="x", assigned_role="vms_ppe_contact",
                    assigned_user_id=user.id, title=task_type, is_completed=completed))
        await db.commit()


async def test_janitor_with_prepare_ppe_task_can_open_visit(test_engine, requester):
    host, client = requester
    janitor = await make_user(test_engine, role="requester")
    vid = await _visit(client, host.id)
    async with authed_client(make_token(janitor.id, janitor.role)) as jc:
        assert (await jc.get(f"/api/v1/visits/{vid}")).status_code == 404  # no task yet
        await _task(vid, janitor)
        assert (await jc.get(f"/api/v1/visits/{vid}")).status_code == 200
        # …and it stays visible after the task is done (audit / history).
        listed = (await jc.get("/api/v1/visits", params={"page_size": 100})).json()
        assert vid in [i["id"] for i in listed["items"]]


async def test_approver_from_other_department_can_open_visit(test_engine, requester):
    host, client = requester
    mgr = await make_user(test_engine, role="dept_manager", department_id=uuid.uuid4())
    vid = await _visit(client, host.id)
    await _task(vid, mgr, task_type="approve_vms_visit", completed=True)
    async with authed_client(make_token(mgr.id, mgr.role)) as mc:
        assert (await mc.get(f"/api/v1/visits/{vid}")).status_code == 200


async def test_unrelated_user_still_cannot_see(test_engine, requester):
    host, client = requester
    other = await make_user(test_engine, role="requester")
    someone = await make_user(test_engine, role="requester")
    vid = await _visit(client, host.id)
    await _task(vid, someone)
    async with authed_client(make_token(other.id, other.role)) as oc:
        assert (await oc.get(f"/api/v1/visits/{vid}")).status_code == 404
