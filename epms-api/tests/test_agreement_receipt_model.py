"""agreement_receipts / agreement_receipt_attachments — model roundtrip."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
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
        email=f"receipt-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Receipt Tester", role="procurement_officer"))
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


async def test_receipt_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        receipt = AgreementReceipt(
            agreement_id=agr.id, receipt_date=date(2026, 7, 27), receipt_ref="1-510076",
            amount=Decimal("100.00"), tax_amount=Decimal("13.00"),
            total_amount=Decimal("113.00"), received_by=user.id, created_by=user.id,
            status="open")
        db.add(receipt)
        await db.commit()
        receipt_id = receipt.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == receipt_id))).scalar_one()
        assert got.receipt_ref == "1-510076"
        assert got.status == "open"
        assert got.invoice_id is None
        assert got.missing_receipt_reason is None


async def test_receipt_ref_may_be_null_and_nulls_do_not_collide(test_engine):
    """基线匹配不依赖编号，所以 receipt_ref 可空；部分唯一索引必须放过多行 NULL。"""
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(3):
            db.add(AgreementReceipt(
                agreement_id=agr.id, receipt_date=date(2026, 7, 1), receipt_ref=None,
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), received_by=user.id,
                created_by=user.id, status="open"))
        await db.commit()
        rows = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.agreement_id == agr.id))).scalars().all()
        assert len(rows) == 3


async def test_receipt_ref_unique_index_is_really_partial(test_engine):
    """`test_receipt_ref_may_be_null_and_nulls_do_not_collide` above cannot fail:
    Postgres already treats NULL as distinct from NULL in a unique index, so
    three NULL rows insert cleanly even against a TOTAL (non-partial) unique
    index. That test alone would not notice `postgresql_where=sa.text(...)`
    quietly disappearing from the model or the migration — and that predicate
    is exactly what lets a receipt with no usable reference number (smudged
    paper, a vendor that just doesn't print one) work end to end. So read the
    index's real DDL back out of the LIVE test database (built by
    `create_all` from the model — the copy a migration-only declaration would
    leave unprotected) and assert the WHERE predicate naming receipt_ref is
    actually there.
    """
    async with test_engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = "
            "'uq_agr_receipt_ref_per_agreement' AND tablename = 'agreement_receipts'"))
        indexdef = result.scalar_one()
    parts = indexdef.split("WHERE", 1)
    assert len(parts) == 2, f"index has no WHERE predicate: {indexdef!r}"
    predicate = parts[1]
    assert "receipt_ref" in predicate
    assert "IS NOT NULL" in predicate.upper()


async def test_duplicate_receipt_ref_on_same_agreement_is_rejected(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for _ in range(2):
            db.add(AgreementReceipt(
                agreement_id=agr.id, receipt_date=date(2026, 7, 1), receipt_ref="DUP-1",
                amount=Decimal("10.00"), tax_amount=Decimal("0"),
                total_amount=Decimal("10.00"), received_by=user.id,
                created_by=user.id, status="open"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_attachment_roundtrip(test_engine):
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        receipt = AgreementReceipt(
            agreement_id=agr.id, receipt_date=date(2026, 7, 1), amount=Decimal("10.00"),
            tax_amount=Decimal("0"), total_amount=Decimal("10.00"),
            received_by=user.id, created_by=user.id, status="open")
        db.add(receipt)
        await db.flush()
        att = AgreementReceiptAttachment(
            receipt_id=receipt.id, filename="receipt.jpg", content_type="image/jpeg",
            file_size=2048, storage_key=uuid.uuid4())
        db.add(att)
        await db.commit()
        att_id = att.id

    async with _factory(test_engine)() as db:
        got = (await db.execute(select(AgreementReceiptAttachment).where(
            AgreementReceiptAttachment.id == att_id))).scalar_one()
        assert got.filename == "receipt.jpg"
        assert got.file_data is None


async def test_invoice_carries_receipt_columns(test_engine):
    from app.models.invoice import Invoice
    cols = {c.name for c in Invoice.__table__.columns}
    assert "receipt_ids" in cols
    assert "receipt_variance_reason" in cols


async def test_receipt_type_defaults_to_counter_slip(test_engine):
    """不传 receipt_type 时落 counter_slip —— 存量语义(柜台小票)就是默认。"""
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        r = AgreementReceipt(
            agreement_id=agr.id, receipt_date=date(2026, 8, 1),
            amount=Decimal("10.00"), tax_amount=Decimal("1.30"),
            total_amount=Decimal("11.30"), received_by=user.id,
            created_by=user.id,
        )
        db.add(r)
        await db.flush()
        await db.refresh(r)
        assert r.receipt_type == "counter_slip"


async def test_all_three_receipt_types_persist(test_engine):
    """delivery / service 与 counter_slip 是并列的一等类型,不是特例。"""
    async with _factory(test_engine)() as db:
        agr, user = await _seed_agreement(db)
        for t in ("counter_slip", "delivery", "service"):
            db.add(AgreementReceipt(
                agreement_id=agr.id, receipt_type=t,
                receipt_date=date(2026, 8, 1), amount=Decimal("10.00"),
                tax_amount=Decimal("0.00"), total_amount=Decimal("10.00"),
                received_by=user.id, created_by=user.id,
            ))
        await db.flush()
        rows = (await db.execute(
            select(AgreementReceipt.receipt_type).where(
                AgreementReceipt.agreement_id == agr.id)
        )).scalars().all()
        assert sorted(rows) == ["counter_slip", "delivery", "service"]
