"""I3 (final whole-branch review, feature/approval-delegation, 2026-08-19):
delegation must widen document visibility only for the delegator's approve*
tasks, never for other task types (create_po, create_pa, match_invoice,
acknowledge_gr, ...).

Four places already enforced "approval tasks only" for the delegated arm
(app/crud/task.py's inbox query, app/crud/dashboard.py's _task_subq, and both
expense-api sibling arms). Two did NOT: access_scope.py's
`_open_task_doc_ids` (feeding the PR/PO/agreement task-chain visibility
helpers) and invoice.py's two `task_user_ids` call sites (get_all ~L218,
is_visible ~L285) — both widened on ANY of the delegator's open tasks. A
delegate covering someone who merely holds a create_po / match_invoice task
(read-only workload, not an approval decision) gained read access to that
document, contradicting the spec's stated scope and the pattern already used
everywhere else.

Each pair of tests below proves both halves of the fix: the delegated
non-approval task must NOT widen visibility, while the delegator's approve
task still does (unaffected). A separate regression guard proves the
VIEWER's OWN non-approval task keeps granting them visibility exactly as
before — only the DELEGATED portion narrows to approve%.
"""
import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access_scope import build_scope, is_po_visible
from app.core.delegation import local_today
from app.crud import invoice as invoice_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _user(db, role="requester"):
    return await user_crud.create(db, RegisterRequest(
        email=f"i3-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="I3 User", role=role))


async def _vendor(db):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="I3 Vendor",
               category="Services", contact_name="C", contact_email="c@i3.test")
    db.add(v)
    await db.flush()
    return v


async def _po(db, *, vendor, created_by, status="submitted"):
    po = PurchaseOrder(number=f"PO-I3-{uuid.uuid4().hex[:6]}", title="I3 PO", type=2,
                        vendor_id=vendor.id, vendor_name=vendor.name, created_by=created_by,
                        status=status, total=Decimal("100.00"))
    db.add(po)
    await db.flush()
    return po


async def _invoice(db, *, vendor, uploaded_by):
    from datetime import date
    inv = Invoice(
        internal_ref=f"INV-I3-{uuid.uuid4().hex[:6]}",
        vendor_invoice_number=f"VN-{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal("100.00"), tax_amount=Decimal("0"), total_amount=Decimal("100.00"),
        invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
        uploaded_by=uploaded_by,
    )
    db.add(inv)
    await db.flush()
    return inv


async def _seed_delegation(db, *, delegator_id, delegate_id):
    today = local_today()
    await db.execute(sa.text(
        "INSERT INTO approval_delegations "
        "(id, delegator_user_id, delegate_user_id, start_date, end_date, created_by) "
        "VALUES (:id, :delegator, :delegate, :today, :today, :delegator)"),
        {"id": uuid.uuid4(), "delegator": delegator_id, "delegate": delegate_id, "today": today})


async def _scope_for(db, user_id, role):
    return await build_scope(db, {"sub": str(user_id), "role": role})


# ── PO visibility (access_scope._open_task_doc_ids) ──────────────────────────

async def test_delegated_create_po_task_does_not_widen_po_visibility(test_engine):
    """The delegator's OPEN create_po task (a role-pool, non-approval task
    type) must NOT surface the PO to the delegate."""
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        delegator = await _user(db, role="dept_manager")
        delegate = await _user(db, role="requester")
        await db.flush()
        po = await _po(db, vendor=vendor, created_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="create_po", document_type="po",
                     document_id=po.id, document_number=po.number,
                     assigned_role="dept_manager", assigned_user_id=delegator.id,
                     title="Create PO", is_completed=False))
        await _seed_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
        await db.commit()
        po_id, delegate_id = po.id, delegate.id

    async with sf() as db:
        scope = await _scope_for(db, delegate_id, "requester")
        visible = await is_po_visible(db, po_id, scope)
        assert visible is False, (
            "delegate gained PO visibility via the delegator's non-approval "
            "create_po task — delegation must cover approval tasks only"
        )


async def test_delegated_approve_po_task_does_widen_po_visibility(test_engine):
    """Positive control: an approve_po task DOES widen visibility, exactly as
    before — only non-approval task types are excluded."""
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        delegator = await _user(db, role="dept_manager")
        delegate = await _user(db, role="requester")
        await db.flush()
        po = await _po(db, vendor=vendor, created_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="approve_po", document_type="po",
                     document_id=po.id, document_number=po.number,
                     assigned_role="dept_manager", assigned_user_id=delegator.id,
                     title="Approve PO", is_completed=False))
        await _seed_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
        await db.commit()
        po_id, delegate_id = po.id, delegate.id

    async with sf() as db:
        scope = await _scope_for(db, delegate_id, "requester")
        visible = await is_po_visible(db, po_id, scope)
        assert visible is True, (
            "delegate could not see the PO via the delegator's approve_po "
            "task — approval-task widening must still work"
        )


async def test_own_create_po_task_still_grants_po_visibility_unaffected(test_engine):
    """Regression guard: the VIEWER's OWN create_po task (no delegation
    involved) must keep widening their own visibility exactly as before."""
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        officer = await _user(db, role="requester")
        await db.flush()
        po = await _po(db, vendor=vendor, created_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="create_po", document_type="po",
                     document_id=po.id, document_number=po.number,
                     assigned_role="requester", assigned_user_id=officer.id,
                     title="Create PO", is_completed=False))
        await db.commit()
        po_id, officer_id = po.id, officer.id

    async with sf() as db:
        scope = await _scope_for(db, officer_id, "requester")
        visible = await is_po_visible(db, po_id, scope)
        assert visible is True, (
            "the viewer's OWN create_po task stopped granting visibility — "
            "the approve%-only restriction must apply to the DELEGATED "
            "portion only, never to the viewer's own tasks"
        )


# ── Invoice visibility (crud/invoice.py get_all + is_visible) ────────────────

async def test_delegated_match_invoice_task_excluded_from_invoice_list(test_engine):
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        delegator = await _user(db, role="dept_manager")
        delegate = await _user(db, role="requester")
        await db.flush()
        inv = await _invoice(db, vendor=vendor, uploaded_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="match_invoice", document_type="invoice",
                     document_id=inv.id, document_number=inv.internal_ref,
                     assigned_role="dept_manager", assigned_user_id=delegator.id,
                     title="Match invoice", is_completed=False))
        await _seed_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
        await db.commit()
        delegate_id = delegate.id

    async with sf() as db:
        items, total = await invoice_crud.get_all(db, task_user_id=delegate_id)
        assert total == 0 and items == [], (
            "the delegator's non-approval match_invoice task leaked the "
            "invoice into the delegate's list — delegation covers approval "
            "tasks only"
        )


async def test_delegated_match_invoice_task_does_not_widen_invoice_visibility(test_engine):
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        delegator = await _user(db, role="dept_manager")
        delegate = await _user(db, role="requester")
        await db.flush()
        inv = await _invoice(db, vendor=vendor, uploaded_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="match_invoice", document_type="invoice",
                     document_id=inv.id, document_number=inv.internal_ref,
                     assigned_role="dept_manager", assigned_user_id=delegator.id,
                     title="Match invoice", is_completed=False))
        await _seed_delegation(db, delegator_id=delegator.id, delegate_id=delegate.id)
        await db.commit()
        delegate_id = delegate.id

    async with sf() as db:
        scope = await _scope_for(db, delegate_id, "requester")
        inv2 = (await db.execute(sa.select(Invoice).where(Invoice.id == inv.id))).scalar_one()
        visible = await invoice_crud.is_visible(db, inv2, scope)
        assert visible is False, (
            "delegate gained invoice visibility via the delegator's "
            "non-approval match_invoice task"
        )


async def test_own_match_invoice_task_still_grants_invoice_visibility_unaffected(test_engine):
    """Regression guard mirroring the PO case above, for the invoice arm."""
    sf = _factory(test_engine)
    async with sf() as db:
        vendor = await _vendor(db)
        creator = await _user(db, role="requester")
        officer = await _user(db, role="requester")
        await db.flush()
        inv = await _invoice(db, vendor=vendor, uploaded_by=creator.id)
        db.add(Task(id=uuid.uuid4(), type="match_invoice", document_type="invoice",
                     document_id=inv.id, document_number=inv.internal_ref,
                     assigned_role="requester", assigned_user_id=officer.id,
                     title="Match invoice", is_completed=False))
        await db.commit()
        officer_id = officer.id

    async with sf() as db:
        scope = await _scope_for(db, officer_id, "requester")
        inv2 = (await db.execute(sa.select(Invoice).where(Invoice.id == inv.id))).scalar_one()
        visible = await invoice_crud.is_visible(db, inv2, scope)
        assert visible is True, (
            "the viewer's OWN match_invoice task stopped granting invoice "
            "visibility"
        )
