"""Department Manager routing — dept_manager approval steps must resolve to a
SPECIFIC user, never a global broadcast.

Regression for PR-20260620-0001: when the requester's department has no
configured Department Manager, _get_dept_manager_id returns None. The old engine
still created an approve_pr task with assigned_user_id=NULL and
assigned_role="dept_manager" — which get_for_role broadcasts to EVERY user
holding the dept_manager base role, letting unrelated managers across all
departments see (and the routed one 404) the PR. Department-scoped approvals must
fail loudly instead of broadcasting.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.crud.engine import (_get_dept_manager_id, _resolve_supervisor,
                              _routing_department_id, execute_action)
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User


async def _make_draft_pr(db, requester: User) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}",
        title="Engine dept_manager routing test",
        status="draft",
        approval_step_idx=0,
        amount=Decimal("100.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    return pr


async def test_submit_pr_without_dept_manager_raises_not_broadcast(engine_db_session):
    """Requester's department has NO dept_manager → submit must raise, not create
    a NULL-assigned (broadcast) approve_pr task."""
    db = engine_db_session
    dept_id = uuid.uuid4()  # a department with no dept_manager configured
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    db.add(requester)
    await db.flush()
    pr = await _make_draft_pr(db, requester)

    with pytest.raises(ValueError, match="Department Manager"):
        await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")


async def test_submit_pr_with_dept_manager_assigns_specific_user(engine_db_session):
    """Requester's department HAS a dept_manager → approve_pr task is assigned to
    that specific user (not a NULL broadcast)."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    manager = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    db.add_all([requester, manager])
    await db.flush()
    pr = await _make_draft_pr(db, requester)

    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    task = (
        await db.execute(
            select(Task).where(Task.document_id == pr.id, Task.type == "approve_pr")
        )
    ).scalar_one()
    assert task.assigned_user_id == manager.id, "dept_manager task must target a specific user"
    assert task.assigned_user_id is not None, "dept_manager task must not broadcast (NULL user)"


# ── PR.department_id drives routing (Task 3: pr-department-selector) ───────────
#
# purchase_requests.department_id (nullable) lets a requester file a PR under a
# DIFFERENT department than their own (e.g. filing on behalf of another team).
# Department-based approval routing (dept_manager / gm_or_opm / director) must
# follow that explicit selection, falling back to the requester's own
# User.department_id when the PR didn't set one (legacy / same-department PRs).
# The personal supervisor step must stay keyed to the requester regardless.


async def test_pr_department_id_drives_dept_manager_routing(engine_db_session):
    """A PR explicitly filed under dept B (creator's own department is A) must
    route the dept_manager approve task to dept B's manager, not dept A's."""
    db = engine_db_session
    dept_a = uuid.uuid4()
    dept_b = uuid.uuid4()
    manager_a = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_a, is_active=True)
    manager_b = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_b, is_active=True)
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_a, is_active=True)
    db.add_all([manager_a, manager_b, requester])
    await db.flush()

    pr = await _make_draft_pr(db, requester)
    pr.department_id = dept_b  # explicit cross-department filing
    await db.flush()

    # Unit-level: the helper resolves the PR's own department, not the creator's.
    dept_id = await _routing_department_id(db, "pr", pr, requester.id)
    assert dept_id == dept_b
    mgr = await _get_dept_manager_id(db, dept_id)
    assert mgr == manager_b.id

    # Integration: execute_action must route the same way.
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    task = (
        await db.execute(
            select(Task).where(Task.document_id == pr.id, Task.type == "approve_pr")
        )
    ).scalar_one()
    assert task.assigned_user_id == manager_b.id, (
        "PR.department_id must drive dept_manager routing, not the requester's own department"
    )


async def test_null_pr_department_falls_back_to_creator_department(engine_db_session):
    """A PR with no explicit department_id (the common case) must fall back to
    the requester's own department — unchanged legacy behaviour."""
    db = engine_db_session
    dept_a = uuid.uuid4()
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_a, is_active=True)
    db.add(requester)
    await db.flush()
    pr = await _make_draft_pr(db, requester)
    assert pr.department_id is None

    dept_id = await _routing_department_id(db, "pr", pr, requester.id)
    assert dept_id == dept_a


async def test_supervisor_resolution_unaffected_by_pr_department(engine_db_session):
    """_resolve_supervisor stays keyed to the routing user (creator) — the
    personal supervisor relationship must NOT switch just because the PR was
    filed under a different department."""
    db = engine_db_session
    dept_a = uuid.uuid4()
    dept_b = uuid.uuid4()
    supervisor = User(full_name="Test User", id=uuid.uuid4(), role="requester", is_active=True)
    requester = User(
        full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_a,
        is_active=True, supervisor_id=supervisor.id,
    )
    db.add_all([supervisor, requester])
    await db.flush()
    pr = await _make_draft_pr(db, requester)
    pr.department_id = dept_b
    await db.flush()

    sup = await _resolve_supervisor(
        db, requester.id, {str(dept_a): True, str(dept_b): True}
    )
    assert sup == supervisor.id


# ── Agreement (agr) routing ───────────────────────────────────────────────────
# A Purchase Agreement carries its own department_id (chosen at creation) and has
# no PR to trace back through, so _routing_department_id must read it directly.
#
# Regression for the 409 hit on the first real submit in dev: the agreement's
# department (Engineering) had an active Department Manager, but routing fell
# through to the SUBMITTER's department — which was NULL for the procurement /
# system account — and the submit died with "no active Department Manager is
# configured for the requester's department". The document's own department was
# ignored entirely.
#
# This also keeps routing and visibility on the same key: epms-api scopes the PA
# list by PurchaseAgreement.department_id (crud/pa.py). If routing used the
# submitter's department instead, a restricted approver could hold the task yet
# never see the document in their list.

async def _make_draft_agreement(db, creator: User, department_id):
    from datetime import date

    from app.models.agreement import PurchaseAgreement

    agr = PurchaseAgreement(
        number=f"AGR-TEST-{uuid.uuid4().hex[:4]}",
        title="Engine agr routing test",
        status="draft",
        approval_step_idx=0,
        vendor_name="Test Vendor",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        department_id=department_id,
        created_by=creator.id,
    )
    db.add(agr)
    await db.flush()
    return agr


async def test_agr_routes_on_the_agreements_own_department(engine_db_session):
    """The agreement's department drives routing even when the submitter has a
    DIFFERENT department — the document's choice wins, not the submitter's."""
    db = engine_db_session
    agr_dept = uuid.uuid4()
    submitter_dept = uuid.uuid4()
    submitter = User(full_name="Test User", id=uuid.uuid4(), role="procurement_officer",
                     department_id=submitter_dept, is_active=True)
    db.add(submitter)
    await db.flush()
    agr = await _make_draft_agreement(db, submitter, agr_dept)

    resolved = await _routing_department_id(db, "agr", agr, submitter.id)
    assert resolved == agr_dept, "routing must use the agreement's department"
    assert resolved != submitter_dept


async def test_agr_routes_when_the_submitter_has_no_department(engine_db_session):
    """The failing case from dev: submitter.department_id is NULL. Routing must
    still resolve from the agreement rather than returning None (which surfaces
    as a 409 'no active Department Manager for the requester's department')."""
    db = engine_db_session
    agr_dept = uuid.uuid4()
    submitter = User(full_name="Test User", id=uuid.uuid4(), role="system_admin",
                     department_id=None, is_active=True)
    db.add(submitter)
    await db.flush()
    agr = await _make_draft_agreement(db, submitter, agr_dept)

    assert await _routing_department_id(db, "agr", agr, submitter.id) == agr_dept


async def test_agr_without_a_department_falls_back_to_the_submitter(engine_db_session):
    """An agreement with no department of its own keeps the legacy fallback."""
    db = engine_db_session
    submitter_dept = uuid.uuid4()
    submitter = User(full_name="Test User", id=uuid.uuid4(), role="procurement_officer",
                     department_id=submitter_dept, is_active=True)
    db.add(submitter)
    await db.flush()
    agr = await _make_draft_agreement(db, submitter, None)

    assert await _routing_department_id(db, "agr", agr, submitter.id) == submitter_dept


# ── Agreement-linked PA routing ───────────────────────────────────────────────
# A PA raised against a Purchase Agreement has NO purchase order and therefore no
# PR to trace a department from (epms-api crud/pa.py takes the agreement route
# with an empty po_links set, leaving po_id NULL). Routing used to fall all the
# way through to "whoever created the PA" — the agreement's Owner, or the AP
# clerk keying it in — so the Department Manager step landed on THEIR
# department's manager instead of the department filled in on the agreement.
#
# Same key as the agreement itself (the "agr" branch above) and as visibility:
# epms-api scopes the PA list by PurchaseAgreement.department_id, so routing on
# the creator's department could hand the task to a manager who cannot even see
# the document.

async def _make_agreement_pa(db, creator: User, agreement_id):
    from app.models.pa import PaymentApplication

    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}",
        title="Engine agreement PA routing test",
        status="draft",
        approval_step_idx=0,
        payment_amount=Decimal("100.00"),
        vendor_name="Test Vendor",
        po_id=None,
        agreement_id=agreement_id,
        invoice_ids=[],
        created_by=creator.id,
    )
    db.add(pa)
    await db.flush()
    return pa


async def test_agreement_pa_routes_on_the_agreements_department(engine_db_session):
    """The agreement's department drives the PA's routing — NOT the department of
    the person who created the PA (typically the agreement Owner or AP)."""
    db = engine_db_session
    agr_dept = uuid.uuid4()
    creator_dept = uuid.uuid4()
    creator = User(full_name="Test User", id=uuid.uuid4(), role="ap_officer",
                   department_id=creator_dept, is_active=True)
    db.add(creator)
    await db.flush()
    agr = await _make_draft_agreement(db, creator, agr_dept)
    pa = await _make_agreement_pa(db, creator, agr.id)

    resolved = await _routing_department_id(db, "pa", pa, creator.id)
    assert resolved == agr_dept, "routing must use the agreement's department"
    assert resolved != creator_dept


async def test_agreement_pa_routes_when_the_creator_has_no_department(engine_db_session):
    """Creator with a NULL department must not collapse the routing to None —
    which surfaces as a 409 'no active Department Manager' on submit."""
    db = engine_db_session
    agr_dept = uuid.uuid4()
    creator = User(full_name="Test User", id=uuid.uuid4(), role="system_admin",
                   department_id=None, is_active=True)
    db.add(creator)
    await db.flush()
    agr = await _make_draft_agreement(db, creator, agr_dept)
    pa = await _make_agreement_pa(db, creator, agr.id)

    assert await _routing_department_id(db, "pa", pa, creator.id) == agr_dept


async def test_agreement_pa_dept_manager_is_the_agreements_not_the_creators(engine_db_session):
    """End of the chain: the resolved approver is the agreement department's
    manager. Paired with the negative assertion so a lookup that resolved NOBODY
    could not satisfy this test (both departments have a manager configured)."""
    db = engine_db_session
    agr_dept, creator_dept = uuid.uuid4(), uuid.uuid4()
    agr_mgr = User(full_name="Agreement Dept Manager", id=uuid.uuid4(),
                   role="dept_manager", department_id=agr_dept, is_active=True)
    creator_mgr = User(full_name="Creator Dept Manager", id=uuid.uuid4(),
                       role="dept_manager", department_id=creator_dept, is_active=True)
    creator = User(full_name="Agreement Owner", id=uuid.uuid4(), role="staff",
                   department_id=creator_dept, is_active=True)
    db.add_all([agr_mgr, creator_mgr, creator])
    await db.flush()
    agr = await _make_draft_agreement(db, creator, agr_dept)
    pa = await _make_agreement_pa(db, creator, agr.id)

    dept = await _routing_department_id(db, "pa", pa, creator.id)
    assert await _get_dept_manager_id(db, dept) == agr_mgr.id
    assert await _get_dept_manager_id(db, dept) != creator_mgr.id


async def test_po_backed_pa_still_routes_through_its_pr(engine_db_session):
    """No regression for the ordinary PO route: a PA with a PO keeps resolving
    the PR's department (the agreement branch must not shadow it)."""
    db = engine_db_session
    from app.models.po import PurchaseOrder

    pr_dept, creator_dept = uuid.uuid4(), uuid.uuid4()
    requester = User(full_name="Requester", id=uuid.uuid4(), role="staff",
                     department_id=uuid.uuid4(), is_active=True)
    creator = User(full_name="AP Clerk", id=uuid.uuid4(), role="ap_officer",
                   department_id=creator_dept, is_active=True)
    db.add_all([requester, creator])
    await db.flush()
    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}", title="PO route",
        status="approved", approval_step_idx=0, amount=Decimal("100.00"),
        department_id=pr_dept, created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="PO route",
        status="approved", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Test Vendor", pr_id=pr.id, created_by=creator.id,
    )
    db.add(po)
    await db.flush()
    pa = await _make_agreement_pa(db, creator, None)
    pa.po_id = po.id
    await db.flush()

    assert await _routing_department_id(db, "pa", pa, creator.id) == pr_dept
