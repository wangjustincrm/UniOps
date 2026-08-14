"""process_pa task routing to payment_officer (Task 3 of the payment_officer
rollout — Task 1 seeded the role in identity, Task 2 registered it in epms-api's
Access Control Matrix).

Approach note: the brief asks for a test that drives a PA all the way to
fully-approved via the real approval workflow and asserts the emitted
process_pa task carries assigned_role == "payment_officer". That workflow is
delegated to approval-api (epms-api/app/services/approval_client.py ->
POST /approval/v1/approvals/{doc_type}/{doc_id}/action), and in this dev
environment the running uniops_approval_api container belongs to a different
worktree and rejects this suite's test JWTs with 401 -> 502. See test_pa.py's
_make_three_way_po docstring for the same, pre-existing constraint ("PO /action
delegates to approval-api, unavailable in unit DB").

So this file takes the fallback the brief names explicitly: call
app.crud.pa._create_process_pa_task directly against a constructed
PaymentApplication and assert on the Task SQLAlchemy adds to the session. This
is narrower than an end-to-end approval test — it proves the function's own
behaviour, not that every code path which fully-approves a PA reaches it.

IMPORTANT CAVEAT for future readers: _create_process_pa_task itself is not
currently called from anywhere in epms-api's own request handlers (grepped
epms-api/app for `_create_process_pa_task` and `create_process_pa_task`; the
only hit is the definition in app/crud/pa.py). The code path that actually
fires in production when a PA is fully approved is
approval-api/app/crud/engine.py's _post_approve_pa (PA-PO) and
_post_approve_pa_dir (PA-DIR), both of which still hardcode
assigned_role="ap_clerk" as of this commit. Changing epms-api's copy keeps it
correct/consistent (and this test honest), but on its own it does NOT redirect
the real process_pa task in production — that requires the same edit in
approval-api/app/crud/engine.py, which is out of this task's file scope. See
this task's report for the full writeup.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import dashboard as dash_crud
from app.crud import pa as pa_crud
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.schemas.dashboard import DashboardResponse


@pytest.mark.asyncio
async def test_process_pa_task_assigned_to_payment_officer(test_engine):
    """_create_process_pa_task must emit assigned_role="payment_officer" and
    leave assigned_user_id unset (role pool, not a direct assignment) — see
    module docstring for why this calls the crud function directly rather
    than driving a PA through the real approval workflow."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pa = PaymentApplication(
            id=uuid.uuid4(),
            pa_number=f"PA-{uuid.uuid4().hex[:8]}",
            title="Process-Payment Routing Test",
            vendor_id=uuid.uuid4(),
            vendor_name="Test Vendor",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("13.00"),
            payment_amount=Decimal("113.00"),
            currency="CAD",
            status="approved",
            created_by=uuid.uuid4(),
        )

        await pa_crud._create_process_pa_task(db, pa)
        await db.flush()

        result = await db.execute(
            select(Task).where(Task.document_id == pa.id, Task.type == "process_pa")
        )
        task = result.scalar_one()

        assert task.assigned_role == "payment_officer"
        assert task.assigned_user_id is None
        assert task.document_type == "pa"
        assert task.document_number == pa.pa_number


@pytest.mark.asyncio
async def test_dashboard_builder_for_payment_officer(test_engine):
    """The role routing chain (app/crud/dashboard.py build()) must actually
    dispatch role="payment_officer" to build_payment_officer and get back a
    valid DashboardResponse rather than falling through to the requester
    default or raising — an unexercised branch is a 500 waiting for the first
    person who holds the role."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        result = await dash_crud.build(db, "payment_officer", uuid.uuid4())

    assert isinstance(result, DashboardResponse)
    assert result.role == "payment_officer"
    assert len(result.kpis) == 4
    titles = {k.title for k in result.kpis}
    assert "PAs Awaiting Payment" in titles
    assert "Processed This Month" in titles
    # Scoped to payment work, not ap_clerk's invoice-matching KPIs.
    assert "Invoices to Match" not in titles
    assert "Exceptions" not in titles
    assert result.pa_in_review is not None
