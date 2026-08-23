"""process_pa task routing to payment_officer.

Correction of an earlier misdiagnosis: this task (payment_officer rollout,
Task 3) originally targeted epms-api/app/crud/pa.py's
`_create_process_pa_task`, which turned out to be dead code — nothing in
epms-api calls it. The real emitters, exercised here, are
app/crud/engine.py's `_post_approve_pa` (PA-PO, doc_type="pa") and
`_post_approve_pa_dir` (PA-DIR / OA Direct PA, doc_type="pa_dir"), both fired
by `execute_action` when a PaymentApplication's last approval step clears.

Unlike the epms-api attempt, `execute_action` is a plain in-process async
function (db, doc_type, doc_id, action, actor_id, actor_role) with no HTTP
hop to a separately-running approval-api container, so these tests drive a
PA all the way to fully-approved through the real engine — no dead-code
fallback needed here. Pattern (single-step workflow_defs override + a
finance_bp holder via user_roles) copied from
test_engine_multiholder_approve.py's `_make_pa_at_gm_step` /
`test_reject_allowed_for_authorized_holder`.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


async def _seed_finance_bp_single_step(db, doc_type: str):
    """A requester (PA creator) + a user holding finance_bp as an ADDITIONAL
    role (identity user_roles — see workflow.py's _post_holders docstring:
    finance_bp is a function, not a singleton post, so it is read from
    user_roles only), plus a CompanyConfig pinning doc_type's workflow to a
    single finance_bp step so approving it is the LAST step."""
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", is_active=True)
    approver = User(full_name="Test User", id=uuid.uuid4(), role="finance_bp", is_active=True)
    db.add_all([requester, approver])
    await db.flush()
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
        {"u": str(approver.id)})
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={
        doc_type: [{"id": "finance_bp", "role": "finance_bp", "label": "Finance BP"}],
    }))
    await db.flush()
    return requester, approver


async def _make_pa(db, *, doc_type: str, created_by, po_id=None):
    pa = PaymentApplication(
        id=uuid.uuid4(),
        pa_number=f"PA-{doc_type.upper()}-{uuid.uuid4().hex[:6]}",
        title=f"{doc_type} full-approval routing test",
        status="submitted",
        approval_step_idx=0,
        payment_amount=Decimal("750.00"),
        vendor_name="Routing Test Vendor",
        currency="CAD",
        invoice_ids=[],
        po_id=po_id,
        created_by=created_by,
    )
    db.add(pa)
    await db.flush()
    return pa


async def _process_pa_task(db, pa_id, document_type):
    result = await db.execute(
        sa.select(Task).where(
            Task.document_id == pa_id,
            Task.type == "process_pa",
            Task.document_type == document_type,
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_pa_po_full_approval_assigns_payment_officer(engine_db_session):
    """PA-PO (doc_type="pa", EPMS purchase payments): the last approval step
    clearing must fire _post_approve_pa and hand the process_pa task to
    payment_officer, not ap_clerk."""
    db = engine_db_session
    requester, approver = await _seed_finance_bp_single_step(db, "pa")
    # po_id=None is fine here: single-step finance_bp routing doesn't consult
    # dept/PO routing at all (see _actor_can_approve's finance_bp branch).
    pa = await _make_pa(db, doc_type="pa", created_by=requester.id, po_id=None)

    result = await execute_action(db, "pa", pa.id, "approve", approver.id, "finance_bp")
    assert result.new_status == "approved"

    task = await _process_pa_task(db, pa.id, "pa")
    assert task.assigned_role == "payment_officer"
    assert task.assigned_user_id is None
    assert task.document_number == pa.pa_number


@pytest.mark.asyncio
async def test_pa_dir_full_approval_assigns_payment_officer(engine_db_session):
    """PA-DIR (doc_type="pa_dir", OA Direct PA — invoices live in
    expense_invoices, not EPMS): the last approval step clearing must fire
    _post_approve_pa_dir and hand the process_pa task to payment_officer, not
    ap_clerk."""
    db = engine_db_session
    requester, approver = await _seed_finance_bp_single_step(db, "pa_dir")
    pa = await _make_pa(db, doc_type="pa_dir", created_by=requester.id, po_id=None)

    result = await execute_action(db, "pa_dir", pa.id, "approve", approver.id, "finance_bp")
    assert result.new_status == "approved"

    task = await _process_pa_task(db, pa.id, "pa_dir")
    assert task.assigned_role == "payment_officer"
    assert task.assigned_user_id is None
    assert task.document_number == pa.pa_number
