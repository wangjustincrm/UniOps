"""Task 6: matching an invoice to a house_account agreement no longer asks
about evidence.

Phase 1A made EVERY house_account match a "legacy settlement" (no evidence,
reason required) because there was no evidence to have. Task 5 built real
evidence — agreement receipts — but bolted mounting them onto /match itself:
picking receipts and declaring "no evidence, here's why" both rode the SAME
request as the pure act of linking an invoice to an agreement. The user's
verdict on that: relating an invoice to an agreement is one thing; relating
an invoice to a receipt is a different thing, and 1A/Task 5 conflated them.

Task 6 tears that back apart. /match is agreement linkage only now, exactly
like recurring and milestone — select an agreement, submit, done. Mounting
receipts and declaring a no-evidence settlement move to the invoice detail
page (Task 7/8); every test in this file that exercised them THROUGH /match
moved with them:
  - test_house_account_match_with_receipts_does_not_flag_legacy
  - test_match_rejects_a_receipt_from_another_agreement
  - test_match_rejects_a_receipt_that_is_{pending_ap_review,reconciled,voided,rejected}
  - test_variance_reason_is_stored_separately_from_legacy_reason
  - test_match_accepts_multiple_receipts
  - test_duplicate_receipt_ids_are_claimed_once_not_rejected
  - test_empty_receipt_ids_list_is_treated_as_no_receipts_selected
  - test_rematch_without_receipts_releases_previously_claimed_receipt
    (its "claim → rematch → released back to open" mechanics are already
    covered independently of /match by test_receipt_release.py, which drives
    _release_agreement_evidence() directly against a receipt claimed by
    manipulating the model — the release invariant this task's brief requires
    _match_to_agreement to keep calling is NOT losing coverage.)
test_house_account_match_without_receipts_still_requires_a_reason is REPLACED
(not moved) by test_house_account_match_needs_no_evidence_and_no_reason below
— same scenario, opposite assertion.

test_deleting_an_invoice_releases_the_receipts_it_claimed stays: it exercises
crud.invoice.delete(), a code path this task does not touch, and is the only
test of that release. Its setup no longer claims the receipt via /match
(which can't do that anymore) — it claims directly through
agreement_receipt_crud.claim(), the same primitive Task 7's mounting
endpoint will call.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_receipt as agreement_receipt_crud
from app.crud.invoice import (
    delete as crud_delete,
    match as crud_match,
)
from app.models.agreement_receipt import AgreementReceipt
from app.models.invoice import Invoice
from app.schemas.agreement_receipt import ReceiptCreate
from app.schemas.invoice import InvoiceMatchRequest
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio


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


async def test_house_account_match_needs_no_evidence_and_no_reason(admin_client, test_engine):
    """匹配就是关联。1A 把这一步做成了"必须交代凭证",而当时系统里根本没有
    任何地方能提供凭证 —— 用户的原始抱怨就是这个。现在它与 recurring /
    milestone 一样:选中协议、提交、结束。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = inv["id"]

    res = await admin_client.post(f"/api/v1/invoices/{inv_id}/match",
                                  json={"agreement_id": str(agr.id)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agreement_id"] == str(agr.id)
    assert body["legacy_settlement"] is False
    assert body["legacy_settlement_reason"] is None
    assert body["receipt_ids"] is None
    assert body["receipt_variance_reason"] is None


async def test_house_account_rematch_to_another_agreement_keeps_a_prior_no_evidence_declaration(
    admin_client, test_engine,
):
    """补充裁定 #2: legacy_settlement 描述的是"这张票没有签收凭证"这个事实,
    与挂在哪份协议无关。一张已经声明过无凭证的发票改挂到另一份协议,那个
    声明依然成立 —— house_account 分支既不重新设它,也不清它。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db_inv.legacy_settlement = True
        db_inv.legacy_settlement_reason = "Backlog statement, no receipt on file"
        db_inv.agreement_id = agr_a.id
        db_inv.status = "matched"
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr_b.id),
                                  matched_by=user_id)

    assert result.agreement_id == agr_b.id
    assert result.legacy_settlement is True
    assert result.legacy_settlement_reason == "Backlog statement, no receipt on file"


async def test_deleting_an_invoice_releases_the_receipts_it_claimed(admin_client, test_engine):
    """Whole-branch review (I4): crud.invoice.delete() — the plain
    DELETE /invoices/{id} path — must release claimed receipts, exactly like the
    Data Maintenance delete path (app/admin/registry.py::_invoice_delete)
    already does. The release was only ever wired into DM, so the two delete
    paths disagreed on the same shared helper.

    The status guard on delete() ("unmatched or exception only") does NOT make
    this unreachable: Data Maintenance declares invoice.status an editable
    enum containing "unmatched" and applies it with a bare setattr and no
    hooks — resetting a matched invoice back to unmatched to re-match it is
    the documented move — which is what the setattr below reproduces.
    Delete it in that state without releasing, and the receipt is stranded
    "reconciled" pointing at a row that no longer exists: update() and void()
    both refuse a reconciled receipt, and _release_agreement_evidence can only
    reach it through invoice.receipt_ids, which the delete just destroyed. That
    receipt can never be claimed by any invoice again.

    Task 6: /match no longer claims receipts (that moves to Task 7's mounting
    endpoint), so this test claims the receipt directly through
    agreement_receipt_crud.claim() — the same primitive that endpoint will
    call — instead of routing it through /match as it used to. What's under
    test here is delete(), not match().
    """
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        matched = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr.id),
                                   matched_by=user_id)
        assert matched.agreement_id == agr.id

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        claimed = await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv)
        db_inv.receipt_ids = [str(r.id) for r in claimed]
        await db.commit()

    async with factory() as db:
        claimed_row = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
        assert claimed_row.status == "reconciled"
        assert claimed_row.invoice_id == inv_id

    # Data Maintenance resetting the status back for a re-match: a bare
    # setattr, no hooks — the same thing app/admin/service.py does.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        db_inv.status = "unmatched"
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_delete(db, db_inv)
        await db.commit()

    async with factory() as db:
        assert (await db.execute(
            select(Invoice).where(Invoice.id == inv_id))).scalar_one_or_none() is None
        released = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
    assert released.status == "open", "receipt stranded as reconciled by a deleted invoice"
    assert released.invoice_id is None
