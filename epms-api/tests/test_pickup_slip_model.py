"""agreement_pickup_slips / agreement_slip_attachments — model roundtrip."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_slip import AgreementPickupSlip
from app.models.agreement_slip_attachment import AgreementSlipAttachment
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed_agreement(db):
    """种子建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是另一条
    未提交的 psycopg2 连接，async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto",
                    category="supplier", contact_name="AP", contact_email="ap@pa.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"slip-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Slip Tester", role="procurement_officer"))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="PA house account",
        agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), created_by=user.id)
    db.add(agr)
    await db.flush()
    return agr, user


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def test_slip_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        slip = AgreementPickupSlip(
            agreement_id=agr.id, slip_date=date(2026, 7, 27), slip_ref="1-510076",
            amount=Decimal("100.00"), tax_amount=Decimal("13.00"),
            total_amount=Decimal("113.00"), picked_by=user.id, created_by=user.id,
            status="open")
        db.add(slip)
        await db.commit()
        slip_id = slip.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == slip_id))).scalar_one()
        assert got.slip_ref == "1-510076"
        assert got.status == "open"
        assert got.invoice_id is None
        assert got.missing_slip_reason is None


async def test_slip_ref_may_be_null_and_nulls_do_not_collide(test_engine):
    """基线匹配不依赖编号，所以 slip_ref 可空；部分唯一索引必须放过多行 NULL。"""
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(3):
            db.add(AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 1), slip_ref=None,
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), picked_by=user.id,
                created_by=user.id, status="open"))
        await db.commit()
        rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.agreement_id == agr.id))).scalars().all()
        assert len(rows) == 3


async def test_duplicate_slip_ref_on_same_agreement_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(2):
            db.add(AgreementPickupSlip(
                agreement_id=agr.id, slip_date=date(2026, 7, 1), slip_ref="DUP-1",
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), picked_by=user.id,
                created_by=user.id, status="open"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_attachment_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        slip = AgreementPickupSlip(
            agreement_id=agr.id, slip_date=date(2026, 7, 1), amount=Decimal("10.00"),
            tax_amount=Decimal("0"), total_amount=Decimal("10.00"),
            picked_by=user.id, created_by=user.id, status="open")
        db.add(slip)
        await db.flush()
        att = AgreementSlipAttachment(
            slip_id=slip.id, filename="slip.jpg", content_type="image/jpeg",
            file_size=2048, storage_key=uuid.uuid4())
        db.add(att)
        await db.commit()
        att_id = att.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementSlipAttachment).where(
            AgreementSlipAttachment.id == att_id))).scalar_one()
        assert got.filename == "slip.jpg"
        assert got.file_data is None


async def test_invoice_carries_slip_columns(test_engine):
    from app.models.invoice import Invoice
    cols = {c.name for c in Invoice.__table__.columns}
    assert "slip_ids" in cols
    assert "slip_variance_reason" in cols
