"""排期行在协议转 active 时生成,且必须幂等。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed(db, **over):
    """种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                    contact_name="AP", contact_email="ap@bell.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    kw = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}", title="Bell", agreement_type="recurring",
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
        overdue_after_days=7, status="active", created_by=user.id,
    )
    kw.update(over)
    agr = PurchaseAgreement(**kw)
    db.add(agr)
    await db.flush()
    return agr


async def test_generates_one_row_per_period_with_agreement_defaults(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        await db.commit()
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence)
        )).scalars().all()
        assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
        assert all(r.expected_amount == Decimal("1200.00") for r in rows)
        assert all(r.tolerance_pct == Decimal("5.00") for r in rows)
        assert all(r.overdue_after_days == 7 for r in rows)
        assert all(r.status == "pending" for r in rows)
        assert all(r.invoice_id is None for r in rows)


async def test_is_idempotent(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()


async def test_non_recurring_agreement_generates_nothing(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, agreement_type="house_account", recurring_type=None,
                          expected_invoice_day=None, expected_amount_per_period=None,
                          tolerance_pct=None)
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()


async def test_null_per_period_amount_leaves_rows_without_an_amount(test_engine):
    # 决策 3:每期金额选填。不填 → 排期行不带金额,认领时不做金额校验。
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, expected_amount_per_period=None)
        await sched_crud.ensure_period_rows(db, agr)
        await db.commit()
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert rows and all(r.expected_amount is None for r in rows)
