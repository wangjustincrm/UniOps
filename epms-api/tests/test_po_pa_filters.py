"""PO gains Type / Prepaid / Department filters; PA gains a Department filter.
Department is resolved through the document chain (PO→PR→cost center;
PA→PO→PR→cost center) since neither PO nor PA carries a cost center directly."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.crud import user as user_crud
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed(test_engine):
    """dept → cost center → PR → PO(Type 4, prepaid) → PA. Returns (po_id, pa_id, dept_id)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"popafilter-{uuid.uuid4().hex[:6]}@t.com", password="TestPass1!",
            full_name="PO/PA Filter", role="system_admin"))
        await db.commit()

        dept = Department(code=f"D{uuid.uuid4().hex[:4].upper()}", name="Chain Dept", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        cc = CostCenter(code=f"CC{uuid.uuid4().hex[:4].upper()}", name="Chain CC",
                        is_active=True, department_id=dept.id)
        db.add(cc)
        await db.commit()
        await db.refresh(cc)
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Chain Vendor",
                        category="Services", contact_name="C", contact_email="c@chain.test")
        db.add(vendor)
        await db.commit()
        await db.refresh(vendor)

        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="Chain PR", type=4,
                             created_by=user.id, cost_center_id=cc.id)
        db.add(pr)
        await db.commit()
        await db.refresh(pr)
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="Chain PO", type=4,
                           vendor_id=vendor.id, vendor_name="Chain Vendor", is_prepaid=True,
                           pr_id=pr.id, created_by=user.id)
        db.add(po)
        await db.commit()
        await db.refresh(po)
        pa = PaymentApplication(pa_number=f"PA-{uuid.uuid4().hex[:6]}", title="Chain PA",
                                vendor_id=vendor.id, vendor_name="Chain Vendor", po_id=po.id,
                                subtotal=Decimal("100.00"), payment_amount=Decimal("100.00"),
                                created_by=user.id)
        db.add(pa)
        await db.commit()
        return str(po.id), str(pa.id), str(dept.id)


async def _po_ids(test_engine, **kw):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        items, _ = await po_crud.get_all(db, **kw)
        return [str(p.id) for p in items]


async def _pa_ids(test_engine, **kw):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        items, _ = await pa_crud.get_all(db, **kw)
        return [str(p.id) for p in items]


@pytest.mark.asyncio
async def test_po_filter_by_type(test_engine):
    po_id, _, _ = await _seed(test_engine)
    assert po_id in await _po_ids(test_engine, pr_type=4)
    assert po_id not in await _po_ids(test_engine, pr_type=1)


@pytest.mark.asyncio
async def test_po_filter_by_prepaid(test_engine):
    po_id, _, _ = await _seed(test_engine)
    assert po_id in await _po_ids(test_engine, is_prepaid=True)
    assert po_id not in await _po_ids(test_engine, is_prepaid=False)


@pytest.mark.asyncio
async def test_po_filter_by_department(test_engine):
    po_id, _, dept_id = await _seed(test_engine)
    assert po_id in await _po_ids(test_engine, department_id=uuid.UUID(dept_id))
    assert po_id not in await _po_ids(test_engine, department_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_pa_filter_by_department(test_engine):
    _, pa_id, dept_id = await _seed(test_engine)
    assert pa_id in await _pa_ids(test_engine, department_id=uuid.UUID(dept_id))
    assert pa_id not in await _pa_ids(test_engine, department_id=uuid.uuid4())
