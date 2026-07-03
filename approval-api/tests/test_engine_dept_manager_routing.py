"""Department Manager routing — dept_manager approval steps must resolve to a
SPECIFIC user, never a global broadcast.

Regression for PR-20260620-0001: when the requester's department has no
configured Department Manager, _get_dept_manager_id returns None. The old engine
still created an approve_pr task with assigned_user_id=NULL and
assigned_role="dept_manager" — which get_for_role broadcasts to EVERY user
holding the dept_manager base role, letting unrelated managers across all
departments see (and the routed one 404) the PR. Department-scoped approvals must
fail loudly instead of broadcasting.

NOTE (2026-07-03): The PR workflow now has 4 steps:
    supervisor → dept_manager → director → gm_or_opm
Submit creates the step-0 (supervisor) task. The dept_manager protection fires
when the supervisor step is approved and the engine tries to create the step-1
(dept_manager) task. Tests below verify the protection at the correct step.
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
    """Requester's department has NO dept_manager configured.

    With the 4-step PR workflow (supervisor → dept_manager → director → gm_or_opm),
    submit succeeds and creates the supervisor task. The dept_manager broadcast-
    prevention fires when supervisor approves and the engine tries to create the
    next (dept_manager) task — at that point it must raise, not create a
    NULL-assigned (broadcast) task.
    """
    db = engine_db_session
    dept_id = uuid.uuid4()  # a department with no dept_manager configured
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    supervisor = User(id=uuid.uuid4(), role="supervisor", is_active=True)
    db.add_all([requester, supervisor])
    await db.flush()
    pr = await _make_draft_pr(db, requester)

    # Submit succeeds — creates the supervisor task (step 0)
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    await db.refresh(pr)
    assert pr.status == "submitted"

    # Approving as supervisor tries to advance to dept_manager (step 1);
    # since no dept_manager is configured, it must raise — not broadcast.
    with pytest.raises(ValueError, match="Department Manager"):
        await execute_action(db, "pr", pr.id, "approve", supervisor.id, "supervisor")


async def test_submit_pr_with_dept_manager_assigns_specific_user(engine_db_session):
    """Requester's department HAS a dept_manager.

    After submit (supervisor task created), approving as supervisor advances to
    dept_manager (step 1). That task must be assigned to the specific dept_manager
    for the requester's department — never a NULL broadcast.
    """
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    manager = User(id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    supervisor = User(id=uuid.uuid4(), role="supervisor", is_active=True)
    db.add_all([requester, manager, supervisor])
    await db.flush()
    pr = await _make_draft_pr(db, requester)

    # Submit — creates supervisor task (step 0)
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    # Supervisor approves — engine advances to step 1 and creates dept_manager task
    await execute_action(db, "pr", pr.id, "approve", supervisor.id, "supervisor")

    # Query specifically for the dept_manager task (the latest approve_pr task)
    tasks = (
        await db.execute(
            select(Task)
            .where(Task.document_id == pr.id, Task.type == "approve_pr")
            .order_by(Task.id)
        )
    ).scalars().all()

    # There should be 2 approve_pr tasks: supervisor (completed) and dept_manager (open)
    assert len(tasks) == 2, f"Expected 2 approve_pr tasks, got {len(tasks)}"

    # The open (dept_manager) task must be assigned to the specific manager
    dm_task = next(t for t in tasks if not t.is_completed)
    assert dm_task.assigned_role == "dept_manager", (
        f"Expected dept_manager task, got role={dm_task.assigned_role}"
    )
    assert dm_task.assigned_user_id == manager.id, (
        "dept_manager task must target a specific user"
    )
    assert dm_task.assigned_user_id is not None, (
        "dept_manager task must not broadcast (NULL user)"
    )
