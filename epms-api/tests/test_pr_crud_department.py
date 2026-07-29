"""CRUD-level tests for PurchaseRequest.department_id (Task 2).

Verifies:
  1) create() stores payload.department_id and derives department_name from
     it (not from the cost center's department, even when both are present).
  2) update() on a draft PR can change department_id and re-derives
     department_name to match.
  3) a Type-1 (no cost center) PR still stores department_id / derives its
     name — proves the department derivation doesn't depend on cost_center_id.

Follows the seed pattern established in tests/test_pr_department_backfill.py
(Task 1): build Department/CostCenter/User/PurchaseRequest ORM rows directly
against the `test_engine` fixture via a local async_sessionmaker, rather than
relying on named fixtures that don't exist in this codebase.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import pr as pr_crud
from app.crud import user as user_crud
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.pr import PurchaseRequest
from app.schemas.auth import RegisterRequest
from app.schemas.pr import PrCreate, PrLineItemIn, PrUpdate


@dataclass
class _CreateCtx:
    dept_a_id: uuid.UUID
    dept_a_name: str
    cc_in_dept_a_id: uuid.UUID
    user_id: uuid.UUID


async def _seed_dept_a_and_cc(db: AsyncSession) -> _CreateCtx:
    """Dept A, a cost center that lives in a *different* dept (Dept B), and a
    requester — used to prove department_name comes from the explicit
    department_id, not from the cost center's department."""
    dept_a = Department(code=f"DA-{uuid.uuid4().hex[:6]}", name="Dept A")
    dept_b = Department(code=f"DB-{uuid.uuid4().hex[:6]}", name="Dept B")
    db.add_all([dept_a, dept_b])
    await db.flush()

    cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:6]}", name="CC in Dept B", department_id=dept_b.id)
    db.add(cc)
    await db.flush()

    user = await user_crud.create(db, RegisterRequest(
        email=f"pr-dept-req-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Dept Create Requester", role="requester"))
    await db.flush()

    return _CreateCtx(
        dept_a_id=dept_a.id, dept_a_name=dept_a.name,
        cc_in_dept_a_id=cc.id, user_id=user.id,
    )


@dataclass
class _UpdateCtx:
    pr: PurchaseRequest
    dept_b_id: uuid.UUID
    dept_b_name: str


async def _seed_two_depts_pr_draft(db: AsyncSession) -> _UpdateCtx:
    """A draft PR already assigned to Dept A, plus a second Dept B to move it to."""
    dept_a = Department(code=f"DA-{uuid.uuid4().hex[:6]}", name="Dept A Upd")
    dept_b = Department(code=f"DB-{uuid.uuid4().hex[:6]}", name="Dept B Upd")
    db.add_all([dept_a, dept_b])
    await db.flush()

    user = await user_crud.create(db, RegisterRequest(
        email=f"pr-dept-upd-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Dept Update Requester", role="requester"))
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-{uuid.uuid4().hex[:8]}", title="Draft PR for dept move", type=1,
        status="draft", amount=Decimal("0"),
        department_id=dept_a.id, department_name=dept_a.name,
        created_by=user.id,
    )
    db.add(pr)
    await db.flush()

    return _UpdateCtx(pr=pr, dept_b_id=dept_b.id, dept_b_name=dept_b.name)


@dataclass
class _NoCcCtx:
    dept_a_id: uuid.UUID
    dept_a_name: str
    user_id: uuid.UUID


async def _seed_dept_a_no_cc(db: AsyncSession) -> _NoCcCtx:
    dept_a = Department(code=f"DA-{uuid.uuid4().hex[:6]}", name="Dept A NoCC")
    db.add(dept_a)
    await db.flush()

    user = await user_crud.create(db, RegisterRequest(
        email=f"pr-dept-nocc-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Dept NoCC Requester", role="requester"))
    await db.flush()

    return _NoCcCtx(dept_a_id=dept_a.id, dept_a_name=dept_a.name, user_id=user.id)


@pytest.mark.asyncio
async def test_create_stores_department_and_derives_name(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        ctx = await _seed_dept_a_and_cc(db)
        await db.commit()

        payload = PrCreate(
            title="X", type=1, department_id=ctx.dept_a_id,
            cost_center_id=ctx.cc_in_dept_a_id,  # deliberately in Dept B — must not win
            line_items=[PrLineItemIn(description="d", qty=1, unit="ea", unit_price=1)],
        )
        pr = await pr_crud.create(db, payload, created_by=ctx.user_id)
        await db.commit()

        assert pr.department_id == ctx.dept_a_id
        assert pr.department_name == ctx.dept_a_name  # derived from department_id, not cost center


@pytest.mark.asyncio
async def test_update_draft_changes_department(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        ctx = await _seed_two_depts_pr_draft(db)
        await db.commit()

        updated = await pr_crud.update(db, ctx.pr, PrUpdate(department_id=ctx.dept_b_id))
        await db.commit()

        assert updated.department_id == ctx.dept_b_id
        assert updated.department_name == ctx.dept_b_name


@pytest.mark.asyncio
async def test_create_type1_no_cost_center_still_stores_department(test_engine):
    """Type-1 PRs carry no cost_center_id; department derivation must not
    depend on it."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        ctx = await _seed_dept_a_no_cc(db)
        await db.commit()

        payload = PrCreate(
            title="No CC", type=1, department_id=ctx.dept_a_id,
            line_items=[PrLineItemIn(description="d", qty=1, unit="ea", unit_price=1)],
        )
        pr = await pr_crud.create(db, payload, created_by=ctx.user_id)
        await db.commit()

        assert pr.cost_center_id is None
        assert pr.department_id == ctx.dept_a_id
        assert pr.department_name == ctx.dept_a_name
