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

from app.crud.engine import execute_action
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
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
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
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    manager = User(id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
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
