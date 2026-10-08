"""Preflight: report every gate submit would hit, and report it identically.

Two properties matter here and neither is obvious from reading preflight_submit
on its own:

  1. It does not short-circuit. Execution stops at the first failure, which is
     right for execution and wrong for diagnosis — someone who fixes one thing
     only to be told about the next has been sent around twice.
  2. Its wording is the SAME STRING execution raises. That is the whole reason
     preflight calls the engine's own helpers instead of re-deriving the rules:
     a second copy would answer confidently and, eventually, wrongly. The
     matches-what-submit-raises tests below fail the moment the two drift.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.crud.engine import (MSG_BUDGET_HARD_BLOCK, execute_action,
                             msg_cannot_submit, preflight_submit)
from app.models.config import CompanyConfig
from app.models.pr import PurchaseRequest
from app.models.user import User


async def _requester(db, dept_id=None) -> User:
    user = User(id=uuid.uuid4(), full_name="Preflight Tester", role="requester",
                department_id=dept_id, is_active=True)
    db.add(user)
    await db.flush()
    return user


async def _dept_manager(db, dept_id) -> User:
    mgr = User(id=uuid.uuid4(), full_name="Preflight Manager", role="dept_manager",
               department_id=dept_id, is_active=True)
    db.add(mgr)
    await db.flush()
    return mgr


async def _draft_pr(db, requester, *, status="draft", over_budget=False) -> PurchaseRequest:
    pr = PurchaseRequest(
        number=f"PR-PF-{uuid.uuid4().hex[:6]}",
        title="Preflight test PR",
        status=status,
        approval_step_idx=0,
        amount=Decimal("100.00"),
        over_budget=over_budget,
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    return pr


def _check(checks, check_id):
    return next(c for c in checks if c.id == check_id)


# ── routing ───────────────────────────────────────────────────────────────────


async def test_unconfigured_department_is_reported_as_not_the_users_problem(engine_db_session):
    db = engine_db_session
    requester = await _requester(db, dept_id=uuid.uuid4())  # no manager in it
    pr = await _draft_pr(db, requester)

    checks = await preflight_submit(db, "pr", pr.id)
    routing = _check(checks, "approver_routing")

    assert routing.passed is False
    assert routing.layer == "config"
    # The distinction the assistant speaks from: don't tell this person to go
    # fix something they have no access to.
    assert routing.fixable_by_user is False
    assert routing.owner == "admin"
    assert "Department Manager" in routing.message


async def test_routing_message_matches_what_submit_actually_raises(engine_db_session):
    """The load-bearing assertion: one wording, produced by one code path."""
    db = engine_db_session
    requester = await _requester(db, dept_id=uuid.uuid4())
    pr = await _draft_pr(db, requester)

    predicted = _check(await preflight_submit(db, "pr", pr.id), "approver_routing").message

    with pytest.raises(ValueError) as raised:
        await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    assert predicted == str(raised.value)


async def test_configured_department_passes(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = await _requester(db, dept_id=dept_id)
    await _dept_manager(db, dept_id)
    pr = await _draft_pr(db, requester)

    checks = await preflight_submit(db, "pr", pr.id)

    assert _check(checks, "approver_routing").passed is True
    assert all(c.passed for c in checks)


async def test_preflight_writes_nothing(engine_db_session):
    """A dry run that left the document submitted would be worse than useless."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = await _requester(db, dept_id=dept_id)
    await _dept_manager(db, dept_id)
    pr = await _draft_pr(db, requester)

    await preflight_submit(db, "pr", pr.id)
    await db.refresh(pr)

    assert pr.status == "draft"
    assert pr.approval_step_idx == 0
    from app.models.task import Task
    tasks = (await db.execute(
        select(Task).where(Task.document_id == pr.id))).scalars().all()
    assert tasks == []


# ── state machine ─────────────────────────────────────────────────────────────


async def test_already_submitted_pr_reports_the_status_gate(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = await _requester(db, dept_id=dept_id)
    await _dept_manager(db, dept_id)
    pr = await _draft_pr(db, requester, status="submitted")

    checks = await preflight_submit(db, "pr", pr.id)
    status = _check(checks, "status_allows_submit")

    assert status.passed is False
    assert status.message == msg_cannot_submit("pr", "submitted")
    # Nothing for the user to fix — the document has simply moved on.
    assert status.fixable_by_user is False


async def test_status_message_matches_what_submit_actually_raises(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = await _requester(db, dept_id=dept_id)
    await _dept_manager(db, dept_id)
    pr = await _draft_pr(db, requester, status="submitted")

    predicted = _check(await preflight_submit(db, "pr", pr.id), "status_allows_submit").message

    with pytest.raises(ValueError) as raised:
        await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    assert predicted == str(raised.value)


# ── the point of the whole thing ──────────────────────────────────────────────


async def test_every_gate_is_evaluated_not_just_the_first(engine_db_session):
    """Two things wrong at once → both reported in one pass.

    Execution would raise on the status gate and never reach routing, so the
    user would fix the status, resubmit, and only then learn their department
    has no manager.
    """
    db = engine_db_session
    requester = await _requester(db, dept_id=uuid.uuid4())  # unconfigured dept
    pr = await _draft_pr(db, requester, status="submitted")  # wrong status too

    checks = await preflight_submit(db, "pr", pr.id)

    assert _check(checks, "status_allows_submit").passed is False
    assert _check(checks, "approver_routing").passed is False
    failed = [c.id for c in checks if not c.passed]
    assert len(failed) >= 2, f"expected several gates reported, got {failed}"


async def test_budget_hard_block_is_reported_and_is_the_users_to_fix(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = await _requester(db, dept_id=dept_id)
    await _dept_manager(db, dept_id)
    # The mirror table has no id default — approval-api only ever reads this row.
    db.add(CompanyConfig(id=uuid.uuid4(),
                         budget_admin_config={"over_budget_mode": "hard_block"}))
    await db.flush()
    pr = await _draft_pr(db, requester, over_budget=True)

    checks = await preflight_submit(db, "pr", pr.id)
    budget = _check(checks, "budget_hard_block")

    assert budget.passed is False
    assert budget.message == MSG_BUDGET_HARD_BLOCK
    # Splitting the PR or freeing up budget is squarely the requester's call.
    assert budget.fixable_by_user is True
