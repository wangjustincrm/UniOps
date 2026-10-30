"""Two submit/approve defects found in functional testing.

ISS-0079 — a double-click on Approve ran the approval twice: PR-20261022-0005
got "Approve by dept manager" and the Director auto-skip twice, and two auto-PDFs.
Both requests read the same status and step before either committed. The engine
now locks the document row for the action, and a caller may name the step its
decision is for (`expected_step`) so a late second click cannot approve the next
step instead.

ISS-0166 — submission checked only the step it landed on. A PR whose over-budget
Finance Manager step comes first was submitted in a department with no
Department Manager, then stuck there: every Approve refused with the routing
error. Every step the chain will route to is now checked at submission.
"""
import asyncio
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.crud.engine import execute_action, preflight_submit
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from tests.conftest import ASYNC_URL

pytestmark = pytest.mark.asyncio
PR_FLOW = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
           {"id": "director", "role": "director", "label": "Director"}]
PR_FLOW_3 = PR_FLOW + [{"id": "procurement_manager", "role": "procurement_manager",
                        "label": "Procurement Manager"}]


async def _world(db, *, with_manager: bool, mode: str = "fm_only", flow=PR_FLOW):
    dept_id = uuid.uuid4()
    await db.execute(sa.text("DROP TABLE IF EXISTS departments CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE departments (id uuid PRIMARY KEY, code varchar(50),"
        " name varchar(255), is_active boolean NOT NULL DEFAULT true)"))
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'DBL','Dbl Dept',true)"),
        {"i": str(dept_id)})
    requester = User(full_name="Requester", id=uuid.uuid4(), role="requester",
                     department_id=dept_id, is_active=True)
    fm = User(full_name="Finance Manager", id=uuid.uuid4(), role="finance_manager",
              department_id=uuid.uuid4(), is_active=True)
    users = [requester, fm]
    dm = None
    if with_manager:
        dm = User(full_name="Dept Manager", id=uuid.uuid4(), role="dept_manager",
                  department_id=dept_id, is_active=True)
        users.append(dm)
    cfg = CompanyConfig(id=uuid.uuid4(), workflow_defs={"pr": flow},
                        budget_admin_config={"over_budget_mode": mode})
    db.add_all([*users, cfg])
    await db.flush()
    return requester, fm, dm


async def _draft_pr(db, requester, *, over_budget: bool) -> PurchaseRequest:
    pr = PurchaseRequest(number=f"PR-DBL-{uuid.uuid4().hex[:4]}", title="Double action",
                         status="draft", approval_step_idx=0, amount=Decimal("5.00"),
                         created_by=requester.id, over_budget=over_budget,
                         over_budget_justification="urgent" if over_budget else None)
    db.add(pr)
    await db.flush()
    return pr


# ── ISS-0079 ──────────────────────────────────────────────────────────────────

async def test_concurrent_double_approve_is_applied_once(engine_db_session):
    db = engine_db_session
    requester, _fm, dm = await _world(db, with_manager=True)
    pr = await _draft_pr(db, requester, over_budget=False)
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    await db.commit()
    pr_id, dm_id = pr.id, dm.id

    engine = create_async_engine(ASYNC_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _approve():
        async with maker() as s:
            await execute_action(s, "pr", pr_id, "approve", dm_id, "dept_manager")
            await s.commit()

    try:
        results = await asyncio.gather(_approve(), _approve(), return_exceptions=True)
    finally:
        await engine.dispose()

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    assert isinstance(errors[0], ValueError)
    approvals = (await db.execute(select(ApprovalEvent).where(
        ApprovalEvent.document_id == pr_id, ApprovalEvent.action == "approve",
        ApprovalEvent.step_idx == 0))).scalars().all()
    assert len(approvals) == 1


async def test_approve_for_a_step_already_passed_is_refused(engine_db_session):
    """An administrator may approve any step, so without expected_step the
    second click of a double-click approves the NEXT step too."""
    db = engine_db_session
    requester, _fm, dm = await _world(db, with_manager=True, flow=PR_FLOW_3)
    pr = await _draft_pr(db, requester, over_budget=False)
    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")
    admin = User(full_name="Admin", id=uuid.uuid4(), role="system_admin", is_active=True)
    db.add(admin)
    await db.flush()

    await execute_action(db, "pr", pr.id, "approve", admin.id, "system_admin",
                         expected_step=0)
    await db.refresh(pr)
    assert pr.approval_step_idx == 2   # Director auto-skipped

    with pytest.raises(ValueError, match="already moved past"):
        await execute_action(db, "pr", pr.id, "approve", admin.id, "system_admin",
                             expected_step=0)
    await db.refresh(pr)
    assert pr.status == "in_review" and pr.approval_step_idx == 2

    # The step on screen is still accepted.
    await execute_action(db, "pr", pr.id, "approve", admin.id, "system_admin",
                         expected_step=2)
    await db.refresh(pr)
    assert pr.status == "approved"


# ── ISS-0166 ──────────────────────────────────────────────────────────────────

async def test_over_budget_pr_without_dept_manager_is_refused_at_submit(engine_db_session):
    db = engine_db_session
    requester, _fm, _ = await _world(db, with_manager=False)
    pr = await _draft_pr(db, requester, over_budget=True)

    with pytest.raises(ValueError, match="Cannot route approval: no active Department Manager"):
        await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    await db.refresh(pr)
    assert pr.status == "draft"
    tasks = (await db.execute(select(Task).where(Task.document_id == pr.id))).scalars().all()
    assert tasks == []


async def test_preflight_reports_the_later_dept_manager_step(engine_db_session):
    db = engine_db_session
    requester, _fm, _ = await _world(db, with_manager=False)
    pr = await _draft_pr(db, requester, over_budget=True)

    checks = {c.id: c for c in await preflight_submit(db, "pr", pr.id)}

    assert checks["approver_routing"].passed is False
    assert "Department Manager" in checks["approver_routing"].message


async def test_over_budget_pr_with_dept_manager_still_submits(engine_db_session):
    db = engine_db_session
    requester, fm, _dm = await _world(db, with_manager=True)
    pr = await _draft_pr(db, requester, over_budget=True)

    await execute_action(db, "pr", pr.id, "submit", requester.id, "requester")

    await db.refresh(pr)
    assert pr.status == "submitted" and pr.approval_step_idx == 0
