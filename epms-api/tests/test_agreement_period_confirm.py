"""履约确认任务 + PA 闸门 —— recurring 免 GR 后唯一的代偿。"""
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
from app.models.department import Department
from app.models.invoice import Invoice
from app.models.task import Task
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
    return agr, vendor, user


async def _invoice(db, agr, vendor, user, total="1200.00"):
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number=f"B{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        currency="CAD", invoice_date=date(2026, 2, 3), due_date=date(2026, 3, 3),
        status="unmatched", line_items=[], uploaded_by=user.id,
    )
    db.add(inv)
    await db.flush()
    return inv


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def test_claiming_a_period_creates_a_confirm_task_for_the_owner(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        agr.owner_id = user.id
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        await db.commit()
        task = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"))).scalar_one()
        assert task.assigned_user_id == user.id
        assert task.document_number == f"{agr.number} · 2026-01"
        assert task.is_completed is False


async def test_without_an_owner_the_task_falls_back_to_the_department_manager(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        mgr = await user_crud.create(db, RegisterRequest(
            email=f"mgr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Dept Manager", role="dept_manager"))
        dept = Department(code=f"D-{uuid.uuid4().hex[:6]}", name=f"Eng-{uuid.uuid4().hex[:6]}",
                          is_active=True)
        db.add(dept)
        await db.flush()
        mgr.department_id = dept.id
        agr.owner_id = None
        agr.department_id = dept.id
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        await db.commit()
        task = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"))).scalar_one()
        assert task.assigned_role == "dept_manager"
        assert task.assigned_user_id == mgr.id


async def test_confirm_stamps_accepted_by_and_at(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.create_confirm_task(db, agr, row)
        confirmed = await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()
        assert confirmed.accepted_by == user.id
        assert confirmed.accepted_at is not None


async def test_confirm_completes_only_the_matching_period_task(test_engine):
    # 两期各有一个未完成任务 → 确认第一期后,只有第一期那条被关掉。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        agr.owner_id = user.id
        await sched_crud.ensure_period_rows(db, agr)
        rows = []
        for _ in range(2):
            inv = await _invoice(db, agr, vendor, user)
            r = await sched_crud.claim_next_period(db, agr, inv)
            await sched_crud.create_confirm_task(db, agr, r)
            rows.append(r)
        await sched_crud.confirm_period(db, agr, rows[0].id, user.id)
        await db.commit()
        tasks = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period"
        ).order_by(Task.document_number))).scalars().all()
        done = {t.document_number: t.is_completed for t in tasks}
        assert done[f"{agr.number} · {rows[0].period_label}"] is True
        assert done[f"{agr.number} · {rows[1].period_label}"] is False


async def test_confirming_an_unclaimed_row_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id
        ).order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        with pytest.raises(ValueError, match="nothing to confirm"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()


async def test_confirming_twice_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        await sched_crud.confirm_period(db, agr, row.id, user.id)
        with pytest.raises(ValueError, match="already been confirmed"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()


async def test_milestone_rows_cannot_be_confirmed_in_this_phase(test_engine):
    # 阶段验收是 Phase 1C。本期 milestone 行走到这里必须被明确拒绝,
    # 而不是悄悄写 accepted_by —— 那会让 1C 面对一批语义不明的历史数据。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, agreement_type="milestone", recurring_type=None,
                                        expected_invoice_day=None,
                                        expected_amount_per_period=None, tolerance_pct=None)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(row)
        await db.flush()
        with pytest.raises(ValueError, match="later phase"):
            await sched_crud.confirm_period(db, agr, row.id, user.id)
        await db.commit()
