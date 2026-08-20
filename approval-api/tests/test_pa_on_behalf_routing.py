"""A PA raised on someone else's behalf still routes by the linked PR.

Procurement Officers may create a Payment Application for any PO. Approval
routing must keep following the PR requester (and the PR's selected department)
— never the PA creator — or paying on someone's behalf would silently reroute
the approval chain to the officer's own manager.
"""
import uuid
from decimal import Decimal

from app.crud.engine import _routing_department_id, _routing_user_id
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User


async def test_pa_created_by_officer_routes_by_pr_requester(engine_db_session):
    db = engine_db_session
    dept_requester = uuid.uuid4()
    dept_officer = uuid.uuid4()
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester",
                     department_id=dept_requester, is_active=True)
    officer = User(full_name="Test User", id=uuid.uuid4(), role="procurement_officer",
                   department_id=dept_officer, is_active=True)
    db.add_all([requester, officer])
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="approved", approval_step_idx=0, amount=Decimal("100.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="issued", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Acme", pr_id=pr.id, created_by=officer.id,
    )
    db.add(po)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="draft", approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="Acme", po_id=po.id, po_number=po.number, invoice_ids=[],
        created_by=officer.id,          # <- the officer, NOT the requester
    )
    db.add(pa)
    await db.flush()

    routing_uid = await _routing_user_id(db, "pa", pa)
    assert routing_uid == requester.id, "PA routing must follow the linked PR's requester"

    dept = await _routing_department_id(db, "pa", pa, routing_uid)
    assert dept == dept_requester, "routing department must be the requester's, not the officer's"


async def test_pa_routing_follows_pr_selected_department(engine_db_session):
    """When the PR was filed under an explicitly selected department, an
    on-behalf PA must route to THAT department, not the requester's own."""
    db = engine_db_session
    dept_own = uuid.uuid4()
    dept_selected = uuid.uuid4()
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester",
                     department_id=dept_own, is_active=True)
    officer = User(full_name="Test User", id=uuid.uuid4(), role="procurement_officer",
                   department_id=uuid.uuid4(), is_active=True)
    db.add_all([requester, officer])
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="approved", approval_step_idx=0, amount=Decimal("100.00"),
        created_by=requester.id, department_id=dept_selected,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="issued", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Acme", pr_id=pr.id, created_by=officer.id,
    )
    db.add(po)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="draft", approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="Acme", po_id=po.id, po_number=po.number, invoice_ids=[],
        created_by=officer.id,
    )
    db.add(pa)
    await db.flush()

    routing_uid = await _routing_user_id(db, "pa", pa)
    dept = await _routing_department_id(db, "pa", pa, routing_uid)
    assert dept == dept_selected, "PR.department_id must win over the requester's own department"


async def test_pa_for_pr_less_po_routes_by_officer_department(engine_db_session):
    """A Procurement Officer may pay ANY PO, including a direct PO with no PR
    (pr_id=None) — there is no requisitioner for routing to follow. This is a
    DELIBERATE, pre-existing fallback (unchanged by on-behalf PA creation: AP
    staff already hit this path creating PAs for direct POs before this
    feature), documented here so nothing silently changes it: with no PR to
    resolve, `_routing_user_id` falls back to `doc.created_by` (the officer
    who raised the PA) and `_routing_department_id` falls back to that same
    user's own department."""
    db = engine_db_session
    dept_officer = uuid.uuid4()
    officer = User(full_name="Test User", id=uuid.uuid4(), role="procurement_officer",
                   department_id=dept_officer, is_active=True)
    db.add(officer)
    await db.flush()

    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="No-PR direct PO",
        status="issued", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Acme", pr_id=None, created_by=officer.id,
    )
    db.add(po)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}", title="No-PR direct PO",
        status="draft", approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="Acme", po_id=po.id, po_number=po.number, invoice_ids=[],
        created_by=officer.id,
    )
    db.add(pa)
    await db.flush()

    routing_uid = await _routing_user_id(db, "pa", pa)
    assert routing_uid == officer.id, (
        "with no PR linked, routing must fall back to the PA creator (the officer)"
    )

    dept = await _routing_department_id(db, "pa", pa, routing_uid)
    assert dept == dept_officer, (
        "with no PR linked, the routing department must fall back to the "
        "routing user's (the officer's) own department"
    )
