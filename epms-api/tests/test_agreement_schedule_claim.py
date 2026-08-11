"""排期行认领 —— FIFO、容差、milestone 人工指定。"""
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
from app.models.invoice import Invoice
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
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="Bell", agreement_type="recurring",
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


async def test_claim_takes_the_lowest_pending_sequence(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None and row.sequence == 1 and row.period_label == "2026-01"
        await db.commit()


async def test_claim_skips_already_received_rows(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        first.status = "received"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.sequence == 2
        await db.commit()


async def test_overdue_rows_are_still_claimable(test_engine):
    # 逾期只是"还没来票"的标记,票来了照样该认领 —— 否则缺票告警反而堵死了收票。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        first.status = "overdue"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.sequence == 1 and row.status == "received"
        await db.commit()


async def test_amount_inside_tolerance_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → 允许区间 [1140, 1260]
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1150.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_at_lower_tolerance_boundary_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → lower bound 1140.00 is INSIDE (inclusive).
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1140.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_at_upper_tolerance_boundary_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → upper bound 1260.00 is INSIDE (inclusive).
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1260.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_outside_tolerance_claims_nothing(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1400.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert all(r.status == "pending" for r in rows)
        await db.commit()


async def test_null_expected_amount_skips_the_amount_check(test_engine):
    # 决策 3:每期金额选填 → 不填就不做金额校验,任何金额都认领。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, expected_amount_per_period=None,
                                        tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="99999.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_no_candidate_rows_returns_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        for r in (await db.execute(select(AgreementPaymentSchedule).where(
                AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all():
            r.status = "received"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        await db.commit()


async def test_claimed_row_records_the_invoice_and_flips_to_received(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.status == "received" and row.invoice_id == inv.id
        await db.commit()


async def test_milestone_claim_rejects_a_row_from_another_agreement(test_engine):
    async with _factory(test_engine)() as db:
        agr_a, vendor, user = await _seed(db, agreement_type="milestone",
                                          recurring_type=None, expected_invoice_day=None,
                                          expected_amount_per_period=None, tolerance_pct=None)
        agr_b, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                  expected_invoice_day=None,
                                  expected_amount_per_period=None, tolerance_pct=None)
        foreign = AgreementPaymentSchedule(
            agreement_id=agr_b.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(foreign)
        await db.flush()
        inv = await _invoice(db, agr_a, vendor, user)
        with pytest.raises(ValueError, match="does not belong"):
            await sched_crud.claim_milestone(db, agr_a, inv, foreign.id)
        await db.commit()


async def test_already_claimed_milestone_cannot_be_claimed_again(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, agreement_type="milestone", recurring_type=None,
                                        expected_invoice_day=None,
                                        expected_amount_per_period=None, tolerance_pct=None)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing", status="pending")
        db.add(row)
        await db.flush()
        inv1 = await _invoice(db, agr, vendor, user)
        await sched_crud.claim_milestone(db, agr, inv1, row.id)
        inv2 = await _invoice(db, agr, vendor, user)
        with pytest.raises(ValueError, match="already has an invoice"):
            await sched_crud.claim_milestone(db, agr, inv2, row.id)
        await db.commit()
