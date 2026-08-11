"""Task 5: house_account invoice matching against pickup slips.

Phase 1A made EVERY house_account match a "legacy settlement" (no evidence,
reason required) because there was no evidence to have. Tasks 1-4 built real
evidence — pickup slips — for that route. This is where the two connect:
selecting slips is now the normal path and stops flagging legacy_settlement;
the reason-required fallback narrows to the case where no slip was selected
at all. That narrowing is the point — see test_house_account_match_with_
slips_does_not_flag_legacy's docstring below.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_slip as agreement_slip_crud
from app.crud.invoice import AgreementMatchInvalid, match as crud_match
from app.models.agreement_slip import AgreementPickupSlip
from app.models.invoice import Invoice
from app.schemas.agreement_slip import SlipCreate
from app.schemas.invoice import InvoiceMatchRequest
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio


async def _create_slip(
    db: AsyncSession, agr, user_id: uuid.UUID, *,
    amount: str = "100.00", tax_amount: str = "0.00", total_amount: str | None = None,
    slip_ref: str | None = None,
) -> AgreementPickupSlip:
    total = Decimal(total_amount) if total_amount is not None else Decimal(amount) + Decimal(tax_amount)
    slip = await agreement_slip_crud.create(
        db, agr,
        SlipCreate(
            slip_date=date(2026, 7, 15), slip_ref=slip_ref,
            amount=Decimal(amount), tax_amount=Decimal(tax_amount), total_amount=total,
            picked_by=user_id, missing_slip_reason=None, notes=None,
        ),
        created_by=user_id,
    )
    await db.commit()
    await db.refresh(slip)
    return slip


async def test_house_account_match_with_slips_does_not_flag_legacy(admin_client, test_engine):
    """选了小票 → legacy_settlement 为 False、reason 为 None、slip_ids 落库、
    每张小票转 reconciled 并记 invoice_id。**这是本特性的核心断言** ——
    协议详情那个 "settled without receipt" 计数从此只数真正无凭证的。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slip = await _create_slip(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, slip_ids=[slip.id]), matched_by=user_id)

    assert result.legacy_settlement is False
    assert result.legacy_settlement_reason is None
    assert result.slip_ids == [str(slip.id)]

    async with factory() as db:
        fresh_slip = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id == slip.id)
        )).scalar_one()
    assert fresh_slip.status == "reconciled"
    assert fresh_slip.invoice_id == inv_id


async def test_house_account_match_without_slips_still_requires_a_reason(admin_client, test_engine):
    """一张都不选 → 回到 1A 的无凭证通道:reason 必填,legacy_settlement=True。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="give a reason"):
            await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                             matched_by=user_id)

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, legacy_settlement_reason="Backlog statement"),
            matched_by=user_id)

    assert result.legacy_settlement is True
    assert result.legacy_settlement_reason == "Backlog statement"
    assert result.slip_ids is None


async def test_match_rejects_a_slip_from_another_agreement(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        foreign_slip = await _create_slip(db, agr_b, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="does not belong"):
            await crud_match(db, db_inv, InvoiceMatchRequest(
                agreement_id=agr_a.id, slip_ids=[foreign_slip.id]), matched_by=user_id)

    # The rejected slip must not have been mutated by the failed attempt.
    async with factory() as db:
        fresh = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id == foreign_slip.id)
        )).scalar_one()
    assert fresh.status == "open"
    assert fresh.invoice_id is None


async def _assert_non_open_slip_is_rejected(admin_client, test_engine, status: str) -> None:
    """pending_ap_review / rejected / reconciled / voided 都不可认领。 Shared body
    for the four status-specific tests below — kept as separate `async def`
    tests rather than @pytest.mark.parametrize because this suite has no
    established pattern for combining parametrize with the session-scoped
    `test_engine` fixture, and mixing them here was observed to intermittently
    corrupt the shared test schema (a mid-session drop_all failing on
    business_partners FK dependents) — a pytest-asyncio loop-scope interaction,
    not a bug in the claim() logic itself. Separate tests sidestep it.
    """
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slip = await _create_slip(db, agr, user_id, amount="100.00")

    async with factory() as db:
        row = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id == slip.id)
        )).scalar_one()
        row.status = status
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match=status):
            await crud_match(db, db_inv, InvoiceMatchRequest(
                agreement_id=agr.id, slip_ids=[slip.id]), matched_by=user_id)


async def test_match_rejects_a_slip_that_is_pending_ap_review(admin_client, test_engine):
    await _assert_non_open_slip_is_rejected(admin_client, test_engine, "pending_ap_review")


async def test_match_rejects_a_slip_that_is_already_reconciled(admin_client, test_engine):
    await _assert_non_open_slip_is_rejected(admin_client, test_engine, "reconciled")


async def test_match_rejects_a_slip_that_is_voided(admin_client, test_engine):
    await _assert_non_open_slip_is_rejected(admin_client, test_engine, "voided")


async def test_match_rejects_a_slip_that_is_rejected(admin_client, test_engine):
    await _assert_non_open_slip_is_rejected(admin_client, test_engine, "rejected")


async def test_variance_reason_is_stored_separately_from_legacy_reason(admin_client, test_engine):
    """差额说明写 slip_variance_reason,不碰 legacy_settlement_reason。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slip = await _create_slip(db, agr, user_id, amount="95.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, slip_ids=[slip.id],
            slip_variance_reason="Rounding on the counter receipt"), matched_by=user_id)

    assert result.slip_variance_reason == "Rounding on the counter receipt"
    assert result.legacy_settlement_reason is None
    assert result.legacy_settlement is False


async def test_match_accepts_multiple_slips(admin_client, test_engine):
    """N:1 —— slip_ids 长度 3,三张全部 reconciled。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="300.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slips = [
            await _create_slip(db, agr, user_id, amount="100.00", slip_ref=f"MS-{i}")
            for i in range(3)
        ]
    slip_ids = [s.id for s in slips]

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, slip_ids=slip_ids), matched_by=user_id)

    assert set(result.slip_ids) == {str(sid) for sid in slip_ids}

    async with factory() as db:
        fresh_rows = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id.in_(slip_ids))
        )).scalars().all()
    assert len(fresh_rows) == 3
    assert all(r.status == "reconciled" for r in fresh_rows)
    assert all(r.invoice_id == inv_id for r in fresh_rows)


# ── Task 5 brief 裁定 #2: 重复 id 去重按一张处理,空列表等同没选。────────────

async def test_duplicate_slip_ids_are_claimed_once_not_rejected(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        slip = await _create_slip(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(
            agreement_id=agr.id, slip_ids=[slip.id, slip.id]), matched_by=user_id)

    assert result.slip_ids == [str(slip.id)]


async def test_empty_slip_ids_list_is_treated_as_no_slips_selected(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(AgreementMatchInvalid, match="give a reason"):
            await crud_match(db, db_inv, InvoiceMatchRequest(
                agreement_id=agr.id, slip_ids=[]), matched_by=user_id)
