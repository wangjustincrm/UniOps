"""缺票逾期扫描。"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.tasks.agreement_overdue import sweep_overdue_periods

pytestmark = pytest.mark.asyncio

# _seed / _factory 从 tests/test_agreement_schedule_claim.py 复制过来


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
    return agr, vendor, user


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _row(db, agr, *, days_ago: int, grace: int = 7,
               status: str = "pending", schedule_type: str = "period"):
    row = AgreementPaymentSchedule(
        agreement_id=agr.id, schedule_type=schedule_type, sequence=1,
        expected_date=date.today() - timedelta(days=days_ago) if schedule_type == "period" else None,
        expected_timing=None if schedule_type == "period" else "After signing",
        milestone_name=None if schedule_type == "period" else "Deposit",
        period_label="2026-01" if schedule_type == "period" else None,
        overdue_after_days=grace, status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def test_a_row_past_expected_date_plus_grace_becomes_overdue(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8, grace=7)      # 到票日 + 7 < 今天
        flipped = await sweep_overdue_periods(db)
        await db.commit()
        assert [r.id for r in flipped] == [row.id]
        assert row.status == "overdue"


async def test_the_boundary_day_itself_is_not_overdue(test_engine):
    # 到票日 + grace == 今天 → **不算**逾期。宽限期的最后一天仍在宽限内。
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=7, grace=7)
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_received_rows_are_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=90, status="received")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "received"
        await db.commit()


async def test_milestone_rows_are_never_swept(test_engine):
    """milestone 行没有 expected_date,本就进不了扫描。但实现里的查询必须**显式**
    带 schedule_type='period' —— Phase 1C 若给阶段加了可选日期,靠
    `expected_date IS NULL` 的隐式过滤会静默失效,阶段会被当成逾期期次刷掉。"""
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                expected_invoice_day=None,
                                expected_amount_per_period=None, tolerance_pct=None)
        row = await _row(db, agr, days_ago=90, schedule_type="milestone")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_sweep_uses_the_default_grace_when_the_row_has_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8)
        row.overdue_after_days = None       # 回落默认 7 天
        await db.flush()
        assert [r.id for r in await sweep_overdue_periods(db)] == [row.id]
        await db.commit()
