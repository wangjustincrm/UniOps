"""Stale approve_* task cleanup (Dashboard ↔ Task Inbox reconciliation).

An approve_pr/po/pa task is valid only while its document is 'submitted' or
'in_review' (approval-api engine enforces valid_approve = these two). The live
approve/return/cancel actions complete the task in the same transaction, but
drift happens — config re-syncs, PMS imports, or status flips that bypass the
engine (a PO reaching 'issued', a PA paid via finance-api) — leaving an OPEN
approve task on a terminal document.

Symptom (real prod): PO-719-2607-02, status 'issued', still carried an open
approve_po task. It showed in the Task Inbox (gates only on is_completed) but
NOT on the Dashboard's Pending Approvals (additionally gates on doc status),
so the two surfaces disagreed and the phantom 409'd on click.

get_for_role must self-heal: complete open approve_* tasks whose document is no
longer approvable. A submitted/in_review document must KEEP its approve task.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _user(db):
    return await user_crud.create(db, RegisterRequest(
        email=f"appr-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Approver", role="gm"))


async def _vendor(db):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Approve Vendor",
               category="Services", contact_name="C", contact_email="c@p.test")
    db.add(v)
    await db.flush()
    return v


async def _pr(db, user, status):
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="Approve PR", type=2,
                         status=status, amount=Decimal("100.00"), created_by=user.id)
    db.add(pr)
    await db.flush()
    return pr


async def _po(db, user, vendor, status):
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="Approve PO", type=2,
                       vendor_id=vendor.id, vendor_name=vendor.name, created_by=user.id,
                       status=status, total=Decimal("100.00"), approval_step_idx=1)
    db.add(po)
    await db.flush()
    return po


async def _pa(db, user, vendor, status):
    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:6]}", title="Approve PA",
        po_id=None, po_number=None, vendor_id=vendor.id, vendor_name=vendor.name,
        subtotal=Decimal("100.00"), payment_amount=Decimal("100.00"),
        currency="CAD", status=status, created_by=user.id)
    db.add(pa)
    await db.flush()
    return pa


async def _agreement(db, user, vendor, status):
    agr = PurchaseAgreement(
        number=f"AGR-{uuid.uuid4().hex[:6]}", title="Approve AGR",
        agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date.today() - timedelta(days=10), valid_to=date.today() + timedelta(days=300),
        status=status, not_to_exceed=Decimal("100.00"), created_by=user.id)
    db.add(agr)
    await db.flush()
    return agr


def _approve_task(doc_type, task_type, doc, user):
    # Personally assigned so surfacing is role-agnostic — this test isolates the
    # completion behaviour, not the role-broadcast matching.
    return Task(
        type=task_type, priority="normal", document_type=doc_type,
        document_id=doc.id, document_number=getattr(doc, "number", None) or doc.pa_number,
        assigned_role="gm", assigned_user_id=user.id,
        title=f"Approve {doc_type.upper()}: {doc.id}", amount=Decimal("100.00"),
    )


# Terminal / non-approvable statuses an approve_* task can drift onto.
_PR_TERMINAL = ["approved", "rejected", "returned", "draft", "cancelled"]
_PO_TERMINAL = ["approved", "issued", "partially_received", "fully_received", "closed", "cancelled"]
_PA_TERMINAL = ["approved", "processed", "cancelled", "rejected", "returned"]
# Agreements leave the approvable set the same ways a PO does — plus "active",
# which is where a *successful* agr approval lands (engine._post_approve_agr
# writes "active", not "approved") — and "expired"/"closed" for a future sweeper.
_AGR_TERMINAL = ["active", "expired", "closed", "cancelled", "returned", "draft"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _PO_TERMINAL)
async def test_approve_po_completed_when_po_terminal(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        vendor = await _vendor(db)
        po = await _po(db, user, vendor, status)
        task = _approve_task("po", "approve_po", po, user)
        db.add(task)
        await db.commit()
        task_id = task.id

        tasks = await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "approve_po" and t.document_id == po.id]
        assert surfaced == [], f"approve_po on '{status}' PO must not surface"
        healed = await db.get(Task, task_id)
        assert healed.is_completed is True
        assert healed.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _PR_TERMINAL)
async def test_approve_pr_completed_when_pr_terminal(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        pr = await _pr(db, user, status)
        task = _approve_task("pr", "approve_pr", pr, user)
        db.add(task)
        await db.commit()
        task_id = task.id

        await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        healed = await db.get(Task, task_id)
        assert healed.is_completed is True, f"approve_pr on '{status}' PR must be healed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _PA_TERMINAL)
async def test_approve_pa_completed_when_pa_terminal(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        vendor = await _vendor(db)
        pa = await _pa(db, user, vendor, status)
        task = _approve_task("pa", "approve_pa", pa, user)
        db.add(task)
        await db.commit()
        task_id = task.id

        await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        healed = await db.get(Task, task_id)
        assert healed.is_completed is True, f"approve_pa on '{status}' PA must be healed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["submitted", "in_review"])
async def test_approve_task_kept_when_doc_still_approvable(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        vendor = await _vendor(db)
        po = await _po(db, user, vendor, status)
        db.add(_approve_task("po", "approve_po", po, user))
        await db.commit()

        tasks = await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "approve_po" and t.document_id == po.id]
        assert len(surfaced) == 1, f"'{status}' PO must keep its approve_po task"
        assert surfaced[0].is_completed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _AGR_TERMINAL)
async def test_approve_agr_completed_when_agreement_terminal(test_engine, status):
    """Purchase Agreements were missing from _complete_stale_approve_tasks'
    doc_specs, so an approve_agr task left behind after the agreement left an
    approvable state became exactly the ghost this whole module exists to
    prevent: visible in the Task Inbox, absent from the Dashboard, 409 on
    click."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        vendor = await _vendor(db)
        agr = await _agreement(db, user, vendor, status)
        task = _approve_task("agr", "approve_agr", agr, user)
        db.add(task)
        await db.commit()
        task_id = task.id

        tasks = await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "approve_agr" and t.document_id == agr.id]
        assert surfaced == [], f"approve_agr on '{status}' agreement must not surface"
        healed = await db.get(Task, task_id)
        assert healed.is_completed is True
        assert healed.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["submitted", "in_review"])
async def test_approve_agr_kept_while_agreement_still_approvable(test_engine, status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await _user(db)
        vendor = await _vendor(db)
        agr = await _agreement(db, user, vendor, status)
        db.add(_approve_task("agr", "approve_agr", agr, user))
        await db.commit()

        tasks = await task_crud.get_for_role(db, "gm", user.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "approve_agr" and t.document_id == agr.id]
        assert len(surfaced) == 1, f"'{status}' agreement must keep its approve_agr task"
        assert surfaced[0].is_completed is False
