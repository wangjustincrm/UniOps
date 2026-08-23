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
