"""ISS-0131: the Completed tab says who closed each task.

GET /tasks?is_completed=true carries completed_by / completed_by_name. A task
the system closed (approval engine, self-healing) has no completed_by; the
inbox then shows only when it was closed.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.task import Task
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def test_completed_tasks_name_who_closed_them(admin_client, test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        closer = await user_crud.create(db, RegisterRequest(
            email=f"ap-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Closer Person", role="ap_clerk"))
        now = datetime.now(timezone.utc)
        by_hand = Task(type="match_invoice", priority="normal", document_type="invoice",
                       document_id=uuid.uuid4(), document_number="INV-T-1",
                       assigned_role="ap_clerk", title="Match INV-T-1",
                       is_completed=True, completed_at=now, completed_by=closer.id)
        by_system = Task(type="approve_pr", priority="normal", document_type="pr",
                         document_id=uuid.uuid4(), document_number="PR-T-1",
                         assigned_role="department_manager", title="Approve PR-T-1",
                         is_completed=True, completed_at=now)
        db.add_all([by_hand, by_system])
        await db.commit()
        hand_id, system_id = str(by_hand.id), str(by_system.id)

    res = await admin_client.get("/api/v1/tasks", params={"is_completed": "true"})
    assert res.status_code == 200, res.text
    rows = {t["id"]: t for t in res.json()["items"]}
    assert rows[hand_id]["completed_by"] == str(closer.id)
    assert rows[hand_id]["completed_by_name"] == "Closer Person"
    assert rows[system_id]["completed_by"] is None
    assert rows[system_id]["completed_by_name"] is None

    # The open list carries the fields too, always empty.
    res = await admin_client.get("/api/v1/tasks", params={"is_completed": "false"})
    assert res.status_code == 200, res.text
    assert all(t["completed_by_name"] is None for t in res.json()["items"])
