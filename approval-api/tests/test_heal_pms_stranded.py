"""Tests for scripts/heal_pms_stranded_approvals.py — the PMS-stranded heal.

Verifies the two heal modes against a real engine + DB:
  - mode='task'    : re-materializes the approve task at the PMS-indicated step.
  - mode='approve' : auto-approves the terminal finance_manager step (status
                     'approved' + approved_at + audit event + process_pa task).
And idempotency: a doc that already has an open approve task is left untouched.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User
from scripts.heal_pms_stranded_approvals import _has_open_approve, _heal_one

# Production PA/PO workflow (with the inserted AP Review / ap_clerk step) — the
# empty-config fallback has NO ap_clerk, so step indices would not match prod.
_WF = {
    "pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Dept Manager"},
        {"id": "director", "role": "director", "label": "Director"},
        {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"},
        {"id": "finance_bp", "role": "finance_bp", "label": "Finance BP"},
        {"id": "ap_clerk", "role": "ap_clerk", "label": "AP Review"},
        {"id": "finance_manager", "role": "finance_manager", "label": "Finance Manager"},
    ],
    "po": [
        {"id": "procurement_manager", "role": "procurement_manager", "label": "Procurement Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"},
    ],
}


async def _seed(db, *, status="in_review", step=4, open_role=None):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs=_WF))
    admin = User(id=uuid.uuid4(), role="system_admin", is_active=True)
    requester = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add_all([admin, requester])
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-HEAL-{uuid.uuid4().hex[:6]}", title="heal test", status=status,
        approval_step_idx=step, payment_amount=Decimal("100.00"), vendor_name="Acme",
        created_by=requester.id,
    )
    db.add(pa)
    await db.flush()
    if open_role:
        db.add(Task(type="approve_pa", document_type="pa", document_id=pa.id,
                    document_number=pa.pa_number, assigned_role=open_role, title="x"))
        await db.flush()
    return pa, admin


async def _open_approve(db, pa_id):
    return (await db.execute(select(Task).where(
        Task.document_type == "pa", Task.document_id == pa_id,
        Task.type == "approve_pa", Task.is_completed.is_(False)))).scalars().all()


@pytest.mark.asyncio
async def test_mode_task_creates_broadcast_ap_clerk_task(engine_db_session):
    db = engine_db_session
    pa, admin = await _seed(db, status="in_review", step=4)
    assert not await _open_approve(db, pa.id)  # stranded: no task

    outcome = await _heal_one(db, "pa", pa.pa_number,
                              {"role": "ap_clerk", "step": 4, "mode": "task", "pms_status": "AP REVIEW"},
                              admin.id, datetime.now(timezone.utc))

    assert outcome == "task_created"
    tasks = await _open_approve(db, pa.id)
    assert len(tasks) == 1
    assert tasks[0].assigned_role == "ap_clerk"
    assert tasks[0].assigned_user_id is None  # broadcast


@pytest.mark.asyncio
async def test_mode_approve_auto_approves_finance_manager(engine_db_session):
    db = engine_db_session
    pa, admin = await _seed(db, status="in_review", step=5)

    outcome = await _heal_one(db, "pa", pa.pa_number,
                              {"role": "finance_manager", "step": 5, "mode": "approve",
                               "pms_status": "FN MANAGER APPROVING"},
                              admin.id, datetime.now(timezone.utc))

    assert outcome == "approved"
    await db.refresh(pa)
    assert pa.status == "approved"
    assert pa.approved_at is not None
    # post-approve side effect: a process_pa payment task for AP Clerk
    proc = (await db.execute(select(Task).where(
        Task.document_id == pa.id, Task.type == "process_pa",
        Task.is_completed.is_(False)))).scalars().all()
    assert len(proc) == 1
    # audit trail: an approve event on behalf of the finance_manager
    ev = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pa.id, ApprovalEvent.action == "approve",
        ApprovalEvent.actor_role == "finance_manager"))).scalars().all()
    assert len(ev) == 1
    assert "behalf of Finance Manager" in (ev[0].comment or "")
    # no NEW open approve task (it's approved, not waiting)
    assert not await _open_approve(db, pa.id)


@pytest.mark.asyncio
async def test_idempotent_skips_doc_with_open_task(engine_db_session):
    db = engine_db_session
    pa, admin = await _seed(db, status="in_review", step=4, open_role="ap_clerk")

    outcome = await _heal_one(db, "pa", pa.pa_number,
                              {"role": "ap_clerk", "step": 4, "mode": "task", "pms_status": "AP REVIEW"},
                              admin.id, datetime.now(timezone.utc))

    assert outcome == "already_has_task"
    assert len(await _open_approve(db, pa.id)) == 1  # unchanged
