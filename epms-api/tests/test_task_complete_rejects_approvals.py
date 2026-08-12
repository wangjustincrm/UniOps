"""POST /tasks/{id}/complete must never close an APPROVAL task.

Why this guard exists (2026-08-12, PO-226-2608-01):
The Task Inbox's "Mark Done" button called this endpoint for ANY task type. When
an approver clicked it on an `approve_*` task, the task flipped to completed
WITHOUT the approval engine running — no ApprovalEvent, no step advance. The
document stayed `in_review` with ZERO open approve tasks, and since the Approve
button is gated purely on holding an open approve task (system_admin included,
because "all tasks" of an empty set is still empty), nobody could ever approve it
again. PO-226-2608-01 plus 2 PRs and 2 EXPs were stranded this way.

The button is gone from the frontend; this closes the endpoint itself so no
script, stale bundle, or direct API call can strand a document again. Non-approval
tasks (place_order, collect_goods, …) stay dismissible — that is legitimate inbox
housekeeping and other tests depend on it.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed(test_engine, task_type: str) -> tuple[uuid.UUID, str]:
    """Create an in_review PO with one open task of `task_type`. Returns (task_id, token)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        approver = await user_crud.create(db, RegisterRequest(
            email=f"opm-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Test OPM", role="opm"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="V", category="Services",
                        contact_name="C", contact_email="c@p.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=approver.id,
                           status="in_review", total=Decimal("100.00"), approval_step_idx=1)
        db.add(po)
        await db.flush()
        task = Task(type=task_type, priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number, assigned_role="opm",
                    title=f"{task_type}: {po.number}", is_completed=False)
        db.add(task)
        await db.flush()
        task_id = task.id
        token = create_access_token(str(approver.id), approver.role)
        await db.commit()
    return task_id, token


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["approve_po", "approve_pr", "approve_pa"])
async def test_complete_rejects_approval_tasks(client, test_engine, task_type):
    task_id, token = await _seed(test_engine, task_type)

    resp = await client.post(f"/api/v1/tasks/{task_id}/complete",
                             headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 409, resp.text
    assert "approv" in resp.json()["detail"].lower()

    # The task must still be OPEN — otherwise the document is stranded.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.is_completed is False
        assert task.completed_at is None
        assert task.completed_by is None


@pytest.mark.asyncio
async def test_complete_still_works_for_non_approval_tasks(client, test_engine):
    """Regression guard: ordinary inbox housekeeping must keep working."""
    task_id, token = await _seed(test_engine, "place_order")

    resp = await client.post(f"/api/v1/tasks/{task_id}/complete",
                             headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert task.is_completed is True
        assert task.completed_at is not None
