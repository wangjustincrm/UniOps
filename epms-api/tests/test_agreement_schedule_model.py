"""agreement_payment_schedule / agreement_attachments — model roundtrip."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.agreement_attachment import AgreementAttachment
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed_agreement(db, agreement_type="recurring"):
    """种子在同一个 async session 里建 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(会 FK 违约)。"""
    vendor = Vendor(
        code=f"V-{uuid.uuid4().hex[:8]}", name="Bell Canada", category="supplier",
        contact_name="AP", contact_email="ap@bell.example",
    )
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Agreement Tester", role="procurement_officer",
    ))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
        title="Bell monthly circuit",
        agreement_type=agreement_type,
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"),
        tolerance_pct=Decimal("5.00"), overdue_after_days=7,
        created_by=user.id,
    )
    db.add(agr)
    await db.flush()
    return agr


async def test_period_row_roundtrip(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="period", sequence=1,
            expected_amount=Decimal("1200.00"), expected_date=date(2026, 1, 5),
            status="pending", period_label="2026-01",
            tolerance_pct=Decimal("5.00"), overdue_after_days=7,
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
        )).scalar_one()
        assert got.schedule_type == "period"
        assert got.period_label == "2026-01"
        assert got.expected_timing is None
        assert got.status == "pending"
        assert got.invoice_id is None


async def test_milestone_row_uses_text_timing_not_a_date(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db, agreement_type="milestone")
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing",
            expected_timing="Within 1 week after contract signing",
            amount_pct=Decimal("30.00"), expected_amount=Decimal("15000.00"),
            status="pending",
        )
        db.add(row)
        await db.commit()
        row_id = row.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
        )).scalar_one()
        assert got.expected_date is None
        assert got.expected_timing == "Within 1 week after contract signing"
        assert got.accepted_by is None


async def test_agreement_carries_recurrence_columns(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        agr.anchor_month = 2
        agr.cost_center_id = uuid.uuid4()
        await db.commit()
        agr_id = agr.id

    async with factory() as db:
        got = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr_id)
        )).scalar_one()
        assert got.recurring_type == "monthly"
        assert got.expected_invoice_day == 5
        assert got.anchor_month == 2
        assert got.overdue_after_days == 7
        assert got.cost_center_id is not None


async def test_attachment_roundtrip(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_agreement(db)
        att = AgreementAttachment(
            agreement_id=agr.id, filename="contract.pdf",
            content_type="application/pdf", file_size=1234,
            storage_key=uuid.uuid4(),
        )
        db.add(att)
        await db.commit()
        att_id = att.id

    async with factory() as db:
        got = (await db.execute(
            select(AgreementAttachment).where(AgreementAttachment.id == att_id)
        )).scalar_one()
        assert got.filename == "contract.pdf"
        assert got.file_data is None
