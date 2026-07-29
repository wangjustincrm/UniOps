"""Backfill test for purchase_requests.department_id (Task 1).

Verifies the three backfill SQL statements from the
ac_add_department_id_to_pr migration:
  1) PR with a cost_center_id -> department_id from that cost center's dept
  2) PR with no cost_center_id -> department_id from the creator's dept
  3) department_name realigned to match the resolved department
and that re-running all three statements is idempotent (no change on rerun).

The test DB (epms_test) is built by tests/conftest.py's `test_engine` fixture
via Base.metadata.create_all — NOT via alembic — so this exercises the
backfill SQL directly against ORM-created tables rather than running the
migration itself (see task brief's Step 5 ambiguity resolution).
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.pr import PurchaseRequest
from app.schemas.auth import RegisterRequest

# Reuse the three backfill statements verbatim from the migration
# (ac_add_department_id_to_pr.upgrade).
BACKFILL = [
    """UPDATE purchase_requests pr SET department_id = cc.department_id
       FROM cost_centers cc WHERE pr.cost_center_id = cc.id AND pr.department_id IS NULL""",
    """UPDATE purchase_requests pr SET department_id = u.department_id
       FROM users u WHERE pr.created_by = u.id AND pr.department_id IS NULL""",
    """UPDATE purchase_requests pr SET department_name = d.name
       FROM departments d WHERE pr.department_id = d.id
       AND pr.department_name IS DISTINCT FROM d.name""",
]


@dataclass
class _SeedCtx:
    dept_a_id: uuid.UUID
    dept_a_name: str
    dept_b_id: uuid.UUID
    pr_with_cc: PurchaseRequest
    pr_no_cc: PurchaseRequest


async def _seed_depts_users_cc(db: AsyncSession) -> _SeedCtx:
    """Creates: dept A + dept B, a user in dept B, a cost center in dept A,
    one PR using that cost center (created_by a dept-A-less user), one PR
    with no cost center (created_by the dept-B user)."""
    dept_a = Department(code=f"DA-{uuid.uuid4().hex[:6]}", name="Dept A")
    dept_b = Department(code=f"DB-{uuid.uuid4().hex[:6]}", name="Dept B")
    db.add_all([dept_a, dept_b])
    await db.flush()

    cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:6]}", name="CC in Dept A", department_id=dept_a.id)
    db.add(cc)
    await db.flush()

    # Creator of the cost-center PR has no department of their own — proves
    # department_id came from the cost center, not the creator.
    requester_no_dept = await user_crud.create(db, RegisterRequest(
        email=f"nodept-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="No Dept Requester", role="requester"))

    requester_dept_b = await user_crud.create(db, RegisterRequest(
        email=f"deptb-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Dept B Requester", role="requester", department_id=dept_b.id))
    await db.flush()

    pr_with_cc = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="PR with CC", type=2,
        status="draft", amount=Decimal("100.00"),
        cost_center_id=cc.id, created_by=requester_no_dept.id,
    )
    pr_no_cc = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="PR without CC", type=2,
        status="draft", amount=Decimal("50.00"),
        created_by=requester_dept_b.id,
    )
    db.add_all([pr_with_cc, pr_no_cc])
    await db.flush()

    return _SeedCtx(
        dept_a_id=dept_a.id, dept_a_name=dept_a.name, dept_b_id=dept_b.id,
        pr_with_cc=pr_with_cc, pr_no_cc=pr_no_cc,
    )


@pytest.mark.asyncio
async def test_backfill_from_cost_center_then_creator(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        ctx = await _seed_depts_users_cc(db)
        await db.commit()

        for _ in range(2):  # run twice -> idempotent
            for stmt in BACKFILL:
                await db.execute(text(stmt))
            await db.commit()

        # ctx.pr_with_cc / ctx.pr_no_cc are still in the session identity map
        # with their original in-memory attribute values; the raw SQL above
        # updated the underlying rows without the ORM knowing, and this
        # factory uses expire_on_commit=False, so commit() alone would not
        # pick that up. Explicitly refresh the two objects we care about.
        pr_with_cc, pr_no_cc = ctx.pr_with_cc, ctx.pr_no_cc
        await db.refresh(pr_with_cc)
        await db.refresh(pr_no_cc)

        assert pr_with_cc.department_id == ctx.dept_a_id  # from cost center
        assert pr_no_cc.department_id == ctx.dept_b_id  # from creator
        assert pr_with_cc.department_name == ctx.dept_a_name
