"""Prepayment PA task must name ONE requester, never broadcast.

Regression for the admin alert "Create Prepayment PA: PO-141-2609-01 … the
document has no linked PR": _post_approve_po used to hardcode
assigned_user_id=None ("Broadcast to all requesters"), a copy of the pre-2026-05
epms-api bug that survived the approval-api extraction. Since PO approval is
delegated to this service (epms-api's /po/{id}/action only forwards), that NULL
is what production actually stores — and 'requester' is not a role pool:
epms-api's notifier suppresses the fan-out and emails every system_admin an
alert that blames a missing PR chain, even for POs that plainly have one.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.crud.engine import _post_approve_po, _po_requester_id
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User


async def _make_user(db, role="requester", name="Test User") -> User:
    user = User(id=uuid.uuid4(), role=role, full_name=name,
                department_id=uuid.uuid4(), is_active=True)
    db.add(user)
    await db.flush()
    return user


async def _make_prepaid_po(db, *, created_by: uuid.UUID, pr_id=None) -> PurchaseOrder:
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}",
        title="FDA Facility Registration Renewal",
        status="approved",
        approval_step_idx=0,
        total=Decimal("1000.00"),
        vendor_name="Test Vendor",
        is_prepaid=True,
        pr_id=pr_id,
        created_by=created_by,
    )
    db.add(po)
    await db.flush()
    return po


async def _prepayment_task(db, po_id) -> Task:
    return (await db.execute(
        select(Task).where(Task.document_id == po_id, Task.type == "create_prepayment_pa")
    )).scalar_one()


async def test_prepayment_task_targets_the_pr_creator(engine_db_session):
    """PO carries a PR link → the task goes to whoever raised that PR."""
    db = engine_db_session
    requester = await _make_user(db)
    po_creator = await _make_user(db, role="procurement_officer")
    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}",
        title="FDA Facility Registration Renewal",
        status="approved",
        approval_step_idx=0,
        amount=Decimal("1000.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    po = await _make_prepaid_po(db, created_by=po_creator.id, pr_id=pr.id)

    await _post_approve_po(db, po)
    await db.flush()

    task = await _prepayment_task(db, po.id)
    assert task.assigned_user_id is not None, "NULL broadcasts to every requester"
    assert task.assigned_user_id == requester.id


async def test_prepayment_task_falls_back_to_po_creator_without_pr(engine_db_session):
    """Direct PO (no PR chain) → the PO's own creator owns the task."""
    db = engine_db_session
    po_creator = await _make_user(db)
    po = await _make_prepaid_po(db, created_by=po_creator.id)

    await _post_approve_po(db, po)
    await db.flush()

    task = await _prepayment_task(db, po.id)
    assert task.assigned_user_id == po_creator.id


async def test_prepayment_task_falls_back_when_pr_row_is_gone(engine_db_session):
    """pr_id points at a row that no longer exists → still never NULL."""
    db = engine_db_session
    po_creator = await _make_user(db)
    po = await _make_prepaid_po(db, created_by=po_creator.id, pr_id=uuid.uuid4())

    await _post_approve_po(db, po)
    await db.flush()

    task = await _prepayment_task(db, po.id)
    assert task.assigned_user_id == po_creator.id


async def test_signoff_submitter_beats_the_service_account(engine_db_session):
    """An imported PO's created_by is the nc-sync service account, whose inbox
    nobody opens — prefer whoever actually submitted it for sign-off."""
    db = engine_db_session
    service_account = await _make_user(db, role="system_admin")
    submitter = await _make_user(db)
    po = await _make_prepaid_po(db, created_by=service_account.id)
    po.signoff_submitted_by = submitter.id
    await db.flush()

    resolved = await _po_requester_id(db, po)

    assert resolved == submitter.id


async def test_place_order_task_still_goes_to_the_procurement_pool(engine_db_session):
    """The sibling task is a genuine role pool — it must stay NULL-assigned."""
    db = engine_db_session
    po_creator = await _make_user(db)
    po = await _make_prepaid_po(db, created_by=po_creator.id)

    await _post_approve_po(db, po)
    await db.flush()

    task = (await db.execute(
        select(Task).where(Task.document_id == po.id, Task.type == "place_order")
    )).scalar_one()
    assert task.assigned_user_id is None
    assert task.assigned_role == "procurement_officer"
