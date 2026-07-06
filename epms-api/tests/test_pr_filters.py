"""PR list filters: department (via the PR's cost-center department), PR type,
and prepaid — added alongside the existing status filter."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import pr as pr_crud
from app.crud import user as user_crud
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.pr import PurchaseRequest
from app.schemas.auth import RegisterRequest


async def _seed(test_engine):
    """One dept + cost center + a prepaid Type-4 PR in it. Returns (pr_id, dept_id)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"prfilter-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="PR Filter", role="system_admin"))
        await db.commit()

        dept = Department(code=f"D{uuid.uuid4().hex[:4].upper()}", name="Filter Dept", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        cc = CostCenter(code=f"CC{uuid.uuid4().hex[:4].upper()}", name="Filter CC",
                        is_active=True, department_id=dept.id)
        db.add(cc)
        await db.commit()
        await db.refresh(cc)

        pr = PurchaseRequest(
            number=f"PR-FLT-{uuid.uuid4().hex[:6]}", title="Filter PR", type=4,
            created_by=user.id, cost_center_id=cc.id, is_prepaid=True)
        db.add(pr)
        await db.commit()
        return str(pr.id), str(dept.id)


async def _ids(test_engine, **kw):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        items, _ = await pr_crud.get_all(db, **kw)
        return [str(p.id) for p in items]


@pytest.mark.asyncio
async def test_filter_by_department(test_engine):
    pr_id, dept_id = await _seed(test_engine)
    assert pr_id in await _ids(test_engine, department_id=uuid.UUID(dept_id))
    # a different (random) department must not include it
    assert pr_id not in await _ids(test_engine, department_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_filter_by_type(test_engine):
    pr_id, _ = await _seed(test_engine)
    assert pr_id in await _ids(test_engine, pr_type=4)
    assert pr_id not in await _ids(test_engine, pr_type=1)


@pytest.mark.asyncio
async def test_filter_by_prepaid(test_engine):
    pr_id, _ = await _seed(test_engine)
    assert pr_id in await _ids(test_engine, is_prepaid=True)
    assert pr_id not in await _ids(test_engine, is_prepaid=False)
