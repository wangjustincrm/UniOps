"""凭证释放 —— 排期行与小票必须一起放回,否则重新认领会带着旧痕迹。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.crud.invoice import _release_agreement_evidence
from app.models.agreement import PurchaseAgreement
from app.models.agreement_slip import AgreementPickupSlip
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(db):
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto",
                    category="supplier", contact_name="AP", contact_email="ap@pa.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"rel-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="PA",
        agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31), created_by=user.id)
    db.add(agr)
    await db.flush()
    return agr, vendor, user


async def _invoice(db, vendor, user, total="100.00"):
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number=f"P{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        currency="CAD", invoice_date=date(2026, 8, 1), due_date=date(2026, 9, 1),
        status="matched", line_items=[], uploaded_by=user.id)
    db.add(inv)
    await db.flush()
    return inv


async def _slip(db, agr, user, *, invoice=None, ref=None, total="10.00"):
    slip = AgreementPickupSlip(
        agreement_id=agr.id, slip_date=date(2026, 7, 15), slip_ref=ref,
        amount=Decimal(total), tax_amount=Decimal("0"), total_amount=Decimal(total),
        picked_by=user.id, created_by=user.id,
        status="reconciled" if invoice else "open",
        invoice_id=invoice.id if invoice else None)
    db.add(slip)
    await db.flush()
    return slip


async def test_release_frees_every_claimed_slip_not_just_the_first(test_engine):
    """三张小票必须全部释放。只断言一张会漏掉"只释放了第一张"——
    这是遍历写错时最可能的表现,而且线上表现为部分期次永久锁死。"""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv = await _invoice(db, vendor, user)
        slips = [await _slip(db, agr, user, invoice=inv, ref=f"R-{i}") for i in range(3)]
        inv.slip_ids = [str(s.id) for s in slips]
        inv.slip_variance_reason = "rounding"
        await db.flush()

        await _release_agreement_evidence(db, inv)
        await db.commit()

    async with _factory(test_engine)() as db:
        rows = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id.in_([s.id for s in slips])))).scalars().all()
        assert len(rows) == 3
        assert all(r.status == "open" for r in rows)
        assert all(r.invoice_id is None for r in rows)
        fresh = (await db.execute(select(Invoice).where(Invoice.id == inv.id))).scalar_one()
        assert fresh.slip_ids is None
        assert fresh.slip_variance_reason is None


async def test_release_leaves_slips_claimed_by_another_invoice_alone(test_engine):
    """同协议下别的发票认领的小票不能被抢回来。"""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv_a = await _invoice(db, vendor, user)
        inv_b = await _invoice(db, vendor, user)
        mine = await _slip(db, agr, user, invoice=inv_a, ref="MINE")
        theirs = await _slip(db, agr, user, invoice=inv_b, ref="THEIRS")
        # inv_a 的 slip_ids 里混进了一张其实属于 inv_b 的小票(数据不一致的情形)
        inv_a.slip_ids = [str(mine.id), str(theirs.id)]
        await db.flush()

        await _release_agreement_evidence(db, inv_a)
        await db.commit()

    async with _factory(test_engine)() as db:
        m = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == mine.id))).scalar_one()
        t = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == theirs.id))).scalar_one()
        assert m.status == "open" and m.invoice_id is None
        assert t.status == "reconciled" and t.invoice_id == inv_b.id


async def test_released_slip_can_be_claimed_again(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv1 = await _invoice(db, vendor, user)
        slip = await _slip(db, agr, user, invoice=inv1, ref="REUSE")
        inv1.slip_ids = [str(slip.id)]
        await db.flush()
        await _release_agreement_evidence(db, inv1)
        await db.flush()

        inv2 = await _invoice(db, vendor, user)
        fresh = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == slip.id))).scalar_one()
        assert fresh.status == "open"
        fresh.status = "reconciled"
        fresh.invoice_id = inv2.id
        await db.commit()


async def test_release_is_a_noop_when_the_invoice_holds_no_slips(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        inv = await _invoice(db, vendor, user)
        untouched = await _slip(db, agr, user, ref="UNTOUCHED")
        await _release_agreement_evidence(db, inv)
        await db.commit()

    async with _factory(test_engine)() as db:
        row = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == untouched.id))).scalar_one()
        assert row.status == "open" and row.invoice_id is None
