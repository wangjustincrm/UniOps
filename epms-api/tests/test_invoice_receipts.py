"""Task 7: mounting agreement receipts to an invoice, as its own action.

Task 6 tore matching and receipt-mounting apart: /match is agreement linkage
only now (see test_receipt_match.py). This left a gap — there was NOWHERE
in the product to actually mount a receipt to an invoice, or to declare
"no evidence, here's why". A house_account invoice matched under Task 6 had
no way to clear the PA gate's evidence requirement at all. This file covers
the two endpoints that close that gap:

  - PUT  /invoices/{id}/receipts              — full-overwrite receipt mount
  - POST /invoices/{id}/settle-without-receipt — explicit no-evidence declaration

PUT /receipts is deliberately FULL-OVERWRITE, not an incremental diff: it
always releases every receipt the invoice currently holds
(_release_agreement_evidence) before claiming the ids in the request body.
A diff ("claim the new ones, release the dropped ones") would need two
separate paths to stay correct, and this branch's history
(_release_agreement_evidence's own docstring) shows that's exactly the kind
of thing that drifts — one path gets a release call added, the other
doesn't, and a receipt is stranded `reconciled` forever (update()/void()
both refuse that status; no UI can recover it). Release-then-reclaim has
only one path, so there's nothing to keep in sync.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_receipt as agreement_receipt_crud
from app.models.agreement_receipt import AgreementReceipt
from app.schemas.agreement_receipt import ReceiptCreate
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


async def _create_receipt(
    db: AsyncSession, agr, user_id: uuid.UUID, *,
    amount: str = "100.00", tax_amount: str = "0.00", total_amount: str | None = None,
    receipt_ref: str | None = None,
) -> AgreementReceipt:
    total = Decimal(total_amount) if total_amount is not None else Decimal(amount) + Decimal(tax_amount)
    receipt = await agreement_receipt_crud.create(
        db, agr,
        ReceiptCreate(
            receipt_date=date(2026, 7, 15), receipt_ref=receipt_ref,
            amount=Decimal(amount), tax_amount=Decimal(tax_amount), total_amount=total,
            received_by=user_id, missing_receipt_reason=None, notes=None,
        ),
        created_by=user_id,
    )
    await db.commit()
    await db.refresh(receipt)
    return receipt


async def _matched_invoice(admin_client, vendor_id, agr, amount="100.00"):
    """House_account invoice already matched to `agr` (Task 6 style — pure
    linkage, no evidence)."""
    inv = await _upload_invoice(admin_client, vendor_id, amount=amount)
    res = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                   json={"agreement_id": str(agr.id)})
    assert res.status_code == 200, res.text
    return res.json()


async def _fresh_receipt(test_engine, receipt_id: uuid.UUID) -> AgreementReceipt:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt_id)
        )).scalar_one()


async def test_put_receipts_claims_them_and_clears_legacy(admin_client, test_engine):
    """挂上凭证 → 每份转 reconciled 并记 invoice_id、发票落 receipt_ids、
    legacy_settlement 被清掉(挂了凭证就不再是无凭证结算)。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(receipt.id)]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["receipt_ids"] == [str(receipt.id)]
    assert body["legacy_settlement"] is False
    assert body["legacy_settlement_reason"] is None

    held = await _fresh_receipt(test_engine, receipt.id)
    assert held.status == "reconciled"
    assert held.invoice_id == uuid.UUID(inv_id)


async def test_put_receipts_with_fewer_ids_releases_the_dropped_ones(admin_client, test_engine):
    """★ 全量覆盖语义:先挂 A+B,再 PUT 只有 A → B 必须回到 open 且 invoice_id
    为空。这是本项目反复被咬的释放不变式,少了它 B 会永久卡在 reconciled,
    而 update()/void() 都拒绝该状态,没有任何界面能救它。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="200.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt_a = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="A")
        receipt_b = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="B")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts", json={
        "receipt_ids": [str(receipt_a.id), str(receipt_b.id)],
    })
    assert res.status_code == 200, res.text

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(receipt_a.id)]})
    assert res.status_code == 200, res.text
    assert res.json()["receipt_ids"] == [str(receipt_a.id)]

    held_a = await _fresh_receipt(test_engine, receipt_a.id)
    assert held_a.status == "reconciled"
    assert held_a.invoice_id == uuid.UUID(inv_id)

    dropped_b = await _fresh_receipt(test_engine, receipt_b.id)
    assert dropped_b.status == "open"
    assert dropped_b.invoice_id is None


async def test_put_empty_list_releases_everything(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(receipt.id)]})
    assert res.status_code == 200, res.text

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts", json={"receipt_ids": []})
    assert res.status_code == 200, res.text
    assert res.json()["receipt_ids"] is None

    released = await _fresh_receipt(test_engine, receipt.id)
    assert released.status == "open"
    assert released.invoice_id is None


async def test_put_rejects_a_receipt_from_another_agreement(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr_a, amount="100.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        foreign_receipt = await _create_receipt(db, agr_b, user_id, amount="100.00")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(foreign_receipt.id)]})
    assert res.status_code == 422, res.text

    fresh = await _fresh_receipt(test_engine, foreign_receipt.id)
    assert fresh.status == "open"
    assert fresh.invoice_id is None


async def test_put_rejects_a_receipt_that_is_not_open(admin_client, test_engine):
    """pending_ap_review / rejected / voided 都不可挂;已被本发票认领的
    reconciled 凭证除外 —— 重挂时必须能把自己已持有的那几份再选上。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="200.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        held_receipt = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="HELD")
        blocked_receipt = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="BLOCKED")

    # Claim `held_receipt` first, then flip `blocked_receipt` to pending_ap_review
    # behind the scenes (an AP-review flag added after the fact).
    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(held_receipt.id)]})
    assert res.status_code == 200, res.text

    async with factory() as db:
        row = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == blocked_receipt.id)
        )).scalar_one()
        row.status = "pending_ap_review"
        await db.commit()

    # Re-PUT with the already-held receipt PLUS the now-blocked one: must 422,
    # and must not touch either receipt's state.
    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts", json={
        "receipt_ids": [str(held_receipt.id), str(blocked_receipt.id)],
    })
    assert res.status_code == 422, res.text

    # The already-held receipt must still be re-selectable on its own — this
    # is the "select back your own already-claimed receipts" case.
    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(held_receipt.id)]})
    assert res.status_code == 200, res.text
    assert res.json()["receipt_ids"] == [str(held_receipt.id)]

    still_held = await _fresh_receipt(test_engine, held_receipt.id)
    assert still_held.status == "reconciled"
    assert still_held.invoice_id == uuid.UUID(inv_id)


async def test_variance_reason_is_stored_and_does_not_block(admin_client, test_engine):
    """差额非零照样 200 —— 柜台采购与发票金额对不上是常态(运费/折扣/税差),
    拦死只会把人推回无凭证通道。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="150.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts", json={
        "receipt_ids": [str(receipt.id)],
        "variance_reason": "Delivery fee added at counter, not itemized on receipt",
    })
    assert res.status_code == 200, res.text
    assert res.json()["receipt_variance_reason"] == (
        "Delivery fee added at counter, not itemized on receipt")


async def test_settle_without_receipt_sets_the_flag_and_reason(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    res = await admin_client.post(f"{INV_URL}/{inv_id}/settle-without-receipt",
                                   json={"reason": "Backlog statement, no receipt on file"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["legacy_settlement"] is True
    assert body["legacy_settlement_reason"] == "Backlog statement, no receipt on file"


async def test_settle_without_receipt_requires_a_reason(admin_client, test_engine):
    """空白理由 → 422。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    res = await admin_client.post(f"{INV_URL}/{inv_id}/settle-without-receipt",
                                   json={"reason": ""})
    assert res.status_code == 422, res.text


async def test_settle_without_receipt_releases_any_held_receipts(admin_client, test_engine):
    """声明"无凭证"时若发票还挂着凭证,那些凭证必须释放 —— 否则它们会
    永久卡在 reconciled 并支撑一张自称无凭证的发票。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(receipt.id)]})
    assert res.status_code == 200, res.text

    res = await admin_client.post(f"{INV_URL}/{inv_id}/settle-without-receipt",
                                   json={"reason": "Switching to no-evidence settlement"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["legacy_settlement"] is True
    assert body["receipt_ids"] is None

    released = await _fresh_receipt(test_engine, receipt.id)
    assert released.status == "open"
    assert released.invoice_id is None
