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
page (Task 7/8); every test below that exercised them THROUGH /match's
request body moved with them:
  - test_house_account_match_with_receipts_does_not_flag_legacy
  - test_variance_reason_is_stored_separately_from_legacy_reason
  - test_empty_receipt_ids_list_is_treated_as_no_receipts_selected
(all three asserted something about /match's request-body handling of
receipt_ids / receipt_variance_reason — fields that no longer exist on
InvoiceMatchRequest).

test_house_account_match_without_receipts_still_requires_a_reason is REPLACED
(not moved) by test_house_account_match_needs_no_evidence_and_no_reason below
— same scenario, opposite assertion.

Review fix round 1 — two things that were WRONGLY dropped as "belongs to
Task 7" on first pass, both restored below:

1. test_rematch_without_receipts_releases_previously_claimed_receipt used to
   cover the release invariant this task's brief explicitly requires
   _match_to_agreement to keep (crud/invoice.py:423-424 — "if
   invoice.receipt_ids or invoice.schedule_id: await
   _release_agreement_evidence(...)"). Deleting it left that exact call site
   uncovered: test_receipt_release.py's four tests all drive
   _release_agreement_evidence() DIRECTLY, never through
   _match_to_agreement; test_route_switch_to_po_releases_claimed_receipts and
   test_match_review_reject_releases_claimed_receipts (both in
   test_agreement_invoice_match.py) cover match()'s PO branch and
   review_match()'s reject branch respectively — neither one is
   _match_to_agreement's own release call. Deleting crud/invoice.py:423-424
   entirely left every one of those five tests green. Replaced with
   test_rematch_to_another_agreement_releases_previously_claimed_receipt
   below, which seeds the "invoice holds a claimed receipt" state directly
   via agreement_receipt_crud.claim() (the same primitive
   test_deleting_an_invoice_releases_the_receipts_it_claimed already uses)
   instead of through the removed request field, then rematches through
   crud.invoice.match() — the real entry point — to a DIFFERENT agreement.

2. agreement_receipt_crud.claim()'s four guards (dedup, cross-agreement
   rejection, non-open-status rejection, two-pass validate-before-mutate) are
   still live code — test_deleting_an_invoice_releases_the_receipts_it_claimed
   and test_receipt_pa_gate.py's happy-path test still call claim() directly,
   and Task 7's mounting endpoint will too — but the six tests that exercised
   those guards were deleted outright on the reasoning that they only reached
   claim() THROUGH /match's now-removed receipt_ids field. That reasoning
   covers the request-body semantics (moved above), not the guards
   themselves. Restored below as direct calls to
   agreement_receipt_crud.claim(), the same shape
   test_deleting_an_invoice_releases_the_receipts_it_claimed already uses:
     - test_claim_rejects_a_receipt_from_another_agreement
     - test_claim_rejects_a_receipt_that_is_{pending_ap_review,reconciled,voided,rejected}
     - test_claim_accepts_multiple_receipts
     - test_claim_dedupes_duplicate_receipt_ids

Review fix round 2 — round 1's restoration of the four guards above still
left the FIFTH one (two-pass validate-before-mutate) covered only by single-
id calls, so the atomicity claim() exists to make ("an invalid id anywhere in
the batch must not leave earlier, valid ids partially mutated") had no test
that could ever fail on it. This gap pre-dates Task 6 — claim() has looked
like this since Task 5 — but Task 7 is about to edit this exact function
(the docstring already plans widening the status guard), so an unguarded
two-pass loop right before that edit is the wrong time to leave it dark.
Added test_claim_leaves_earlier_receipts_untouched_when_a_later_id_is_invalid
below: one open (valid) + one voided (invalid) id in the same call, asserts
the valid receipt is unchanged in the DATABASE (re-read through a fresh
session, not the in-memory object the failed call touched).

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
from app.crud.agreement_receipt import _STATUS_WORDS
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


async def test_rematch_to_another_agreement_releases_previously_claimed_receipt(
    admin_client, test_engine,
):
    """Review fix round 1, Important #1: the release invariant the brief
    explicitly requires _match_to_agreement to keep
    (crud/invoice.py:423-424 — release BEFORE the house_account branch's
    now-`pass` body runs) must stay covered by a test that drives
    _match_to_agreement itself, not just _release_agreement_evidence() in
    isolation (test_receipt_release.py) or a DIFFERENT function's release
    call (match()'s PO branch / review_match()'s reject branch, both in
    test_agreement_invoice_match.py). Deleting crud/invoice.py:423-424
    entirely must turn THIS test red even though those others stay green.

    Setup claims the receipt directly via agreement_receipt_crud.claim() —
    the same primitive test_deleting_an_invoice_releases_the_receipts_it_
    claimed uses — since /match can no longer do that itself (Task 7's
    mounting endpoint will)."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr_a, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr_a.id),
                         matched_by=user_id)

    # Claim the receipt directly — the shape Task 7's mounting endpoint will
    # produce, not reachable through /match anymore.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        claimed = await agreement_receipt_crud.claim(db, agr_a, [receipt.id], db_inv)
        db_inv.receipt_ids = [str(r.id) for r in claimed]
        await db.commit()

    async with factory() as db:
        held = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
    assert held.status == "reconciled"
    assert held.invoice_id == inv_id

    # Rematch the SAME invoice to a DIFFERENT agreement — the release call
    # at the top of _match_to_agreement must fire and free the receipt.
    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        result = await crud_match(db, db_inv, InvoiceMatchRequest(agreement_id=agr_b.id),
                                  matched_by=user_id)
    assert result.agreement_id == agr_b.id
    assert result.receipt_ids is None

    async with factory() as db:
        released = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
    assert released.status == "open", "receipt stranded as reconciled by a route switch"
    assert released.invoice_id is None


# ── Review fix round 1, Important #2: agreement_receipt_crud.claim()'s own ──
# guards are live code (called directly by the tests above and by Task 7's
# future mounting endpoint) and need their own coverage — not reachable
# through /match anymore, so these call claim() directly instead of routing
# through crud.invoice.match(). ─────────────────────────────────────────────

async def test_claim_rejects_a_receipt_from_another_agreement(admin_client, test_engine):
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr_a = await _make_active_agreement(test_engine, vendor_id, user_id)
    agr_b = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        foreign_receipt = await _create_receipt(db, agr_b, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(ValueError, match="does not belong"):
            await agreement_receipt_crud.claim(db, agr_a, [foreign_receipt.id], db_inv)

    # The rejected receipt must not have been mutated by the failed attempt.
    async with factory() as db:
        fresh = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == foreign_receipt.id)
        )).scalar_one()
    assert fresh.status == "open"
    assert fresh.invoice_id is None


async def _assert_claim_rejects_non_open_receipt(admin_client, test_engine, status: str) -> None:
    """pending_ap_review / rejected / reconciled / voided 都不可认领 ——
    agreement_receipt_crud.claim() 本身,不经过 /match。Shared body for the
    four status-specific tests below, same reasoning as the pre-Task-6
    version for keeping them as separate `async def` tests rather than
    @pytest.mark.parametrize (session-scoped test_engine fixture interaction)."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    async with factory() as db:
        row = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
        row.status = status
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # Matches the OPERATOR-facing wording, not the storage value: the
        # message names the receipt the way a person can find it and says the
        # state in the words its badge uses ("removed", not "voided"). Pinning
        # the raw status here would pass on a message nobody can act on, which
        # is what this used to be — a bare uuid and a database word.
        with pytest.raises(ValueError, match=_STATUS_WORDS[status]):
            await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv)


async def test_claim_rejects_a_receipt_that_is_pending_ap_review(admin_client, test_engine):
    await _assert_claim_rejects_non_open_receipt(admin_client, test_engine, "pending_ap_review")


async def test_claim_rejects_a_receipt_that_is_already_reconciled(admin_client, test_engine):
    await _assert_claim_rejects_non_open_receipt(admin_client, test_engine, "reconciled")


async def test_claim_rejects_a_receipt_that_is_voided(admin_client, test_engine):
    await _assert_claim_rejects_non_open_receipt(admin_client, test_engine, "voided")


async def test_claim_rejects_a_receipt_that_is_rejected(admin_client, test_engine):
    await _assert_claim_rejects_non_open_receipt(admin_client, test_engine, "rejected")


async def test_claim_accepts_multiple_receipts(admin_client, test_engine):
    """N:1 —— claim() 一次接收 3 张凭证的 id,三张全部转 reconciled。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="300.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipts = [
            await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref=f"MS-{i}")
            for i in range(3)
        ]
    receipt_ids = [s.id for s in receipts]

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        claimed = await agreement_receipt_crud.claim(db, agr, receipt_ids, db_inv)
        await db.commit()

    assert {r.id for r in claimed} == set(receipt_ids)

    async with factory() as db:
        fresh_rows = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id.in_(receipt_ids))
        )).scalars().all()
    assert len(fresh_rows) == 3
    assert all(r.status == "reconciled" for r in fresh_rows)
    assert all(r.invoice_id == inv_id for r in fresh_rows)


async def test_claim_dedupes_duplicate_receipt_ids(admin_client, test_engine):
    """重复 id 去重按一张处理,不因为同一个 id 出现两次而报错。"""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        claimed = await agreement_receipt_crud.claim(db, agr, [receipt.id, receipt.id], db_inv)
        await db.commit()

    assert [r.id for r in claimed] == [receipt.id]


async def test_claim_leaves_earlier_receipts_untouched_when_a_later_id_is_invalid(
    admin_client, test_engine,
):
    """两遍校验的意义:第一遍全部校验通过才进第二遍改状态。一次调用里混入一个
    非法 id 时,前面那些合法的凭证必须**原样不动** —— 否则一次失败的挂载会留下
    一批被半改过的凭证,而它们卡在 reconciled 之后 update()/void() 都拒绝,
    没有任何界面能救。

    Pre-dates Task 6 (claim() itself, not something this task introduced), but
    left uncovered until now: Task 7 is about to touch this exact function
    (docstring already plans to widen the status guard to also accept a
    receipt "reconciled" by THIS SAME invoice) — a two-pass loop with no
    atomicity coverage would let that change silently regress with no signal.

    This is a CRUD-layer contract test, not a simulation of any particular
    caller — as of Task 6, claim() has ZERO production callers at all (the
    house_account branch it used to be invoked from is now a bare `pass`;
    Task 7 is what will give it one). claim()'s atomicity has to hold
    regardless of what that future caller does with its session — commit,
    roll back, or neither — so this test doesn't assume any of those.

    Review fix round 3: an earlier version of this docstring claimed the
    explicit `await db.commit()` below "faithfully reproduces" a real
    caller's behavior. That was wrong on both counts — no such caller
    exists, and even a hypothetical one wouldn't behave that way: /match's
    actual request-scoped session (app/db/session.py's get_session
    dependency) ROLLS BACK on any exception, ValueError included (FastAPI
    throws it into the generator, which hits `except Exception: await
    session.rollback(); raise`) — the opposite of what this test does.

    The commit here exists for a narrower, purely mechanical reason: this
    test DB does not wrap each test in a transaction that gets rolled back
    at teardown (see conftest.py:305-311), so an uncommitted session's
    pending changes are simply discarded when it closes — a single-pass
    (buggy) and a two-pass (correct) implementation would look IDENTICAL
    to a test that never commits, because neither one's partial write would
    ever become observable. Committing is what makes the difference between
    them visible to the fresh-session read below at all; it is not a claim
    about how any real caller is expected to behave.

    Reads the "untouched" receipt back through a FRESH session (not the one
    claim() ran in) — asserting against the in-memory object from the failed
    call would only prove the ORM identity map didn't mutate it, not that the
    database row itself was left alone.
    """
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        open_receipt = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="OPEN-OK")
        voided_receipt = await _create_receipt(db, agr, user_id, amount="50.00", receipt_ref="VOIDED-BAD")

    async with factory() as db:
        row = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == voided_receipt.id)
        )).scalar_one()
        row.status = "voided"
        await db.commit()

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(ValueError, match="removed"):
            await agreement_receipt_crud.claim(
                db, agr, [open_receipt.id, voided_receipt.id], db_inv)
        # Committed on purpose (see docstring above) — NOT a claim about how
        # a real caller behaves (none exists yet, and /match's own session
        # would roll back here anyway). This is purely so a single-pass
        # implementation's partial write becomes observable to the
        # fresh-session read below instead of being discarded for free when
        # this uncommitted, non-transactional-test-DB session closes.
        await db.commit()

    # Fresh session, fresh row — not the one the failed claim() call ran in.
    async with factory() as db:
        fresh_open = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == open_receipt.id)
        )).scalar_one()
    assert fresh_open.status == "open", "an earlier, valid receipt was mutated by a call that failed on a later id"
    assert fresh_open.invoice_id is None


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


# ── Task 7 fix-round 1, Important #3: the widened guard's own two branches ──
# ("already reconciled by THIS invoice" accepted, "reconciled by a DIFFERENT
# invoice" still rejected) had zero direct coverage. The existing
# test_claim_rejects_a_receipt_that_is_already_reconciled (via
# _assert_claim_rejects_non_open_receipt) only sets status="reconciled" while
# leaving invoice_id NULL, which pins the "not this invoice" branch but can't
# tell a correct implementation apart from the review's counter-example
# (`receipt.invoice_id is not None`, which would let ANY invoice steal a
# receipt another invoice already holds) — that buggy variant passes every
# existing test in this file. These two close that gap by exercising claim()
# directly, bypassing set_receipts' own release-first call so the "reconciled
# AND this invoice" branch is what's actually being tested, not release().

async def test_claim_accepts_a_receipt_already_reconciled_by_the_same_invoice(
    admin_client, test_engine,
):
    """The accept side of the widened guard. claim() is idempotent for a
    receipt the SAME invoice already holds — this is the exact recovery path
    the widened guard exists for (see claim()'s docstring): claim it once,
    then claim it again for the same invoice WITHOUT going through
    set_receipts' release-first call, and it must succeed rather than being
    rejected as "already reconciled"."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_id = uuid.UUID(inv["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        first = await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv)
        await db.commit()
    assert [r.id for r in first] == [receipt.id]

    async with factory() as db:
        db_inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # Same invoice, same receipt, no release in between — must not raise.
        second = await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv)
        await db.commit()
    assert [r.id for r in second] == [receipt.id]

    async with factory() as db:
        fresh = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
    assert fresh.status == "reconciled"
    assert fresh.invoice_id == inv_id


async def test_claim_rejects_a_receipt_reconciled_by_a_different_invoice(
    admin_client, test_engine,
):
    """The reject side of the widened guard. Invoice A holds `receipt`;
    invoice B must NOT be able to claim it — the review's counter-example
    (`receipt.invoice_id is not None`, i.e. any non-NULL holder is treated as
    "mine") would let this through. Re-reads the receipt through a FRESH
    session afterward: it must still show A as the holder, unchanged by B's
    rejected attempt."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv_a = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_a_id = uuid.UUID(inv_a["id"])
    inv_b = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    inv_b_id = uuid.UUID(inv_b["id"])

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00")

    async with factory() as db:
        db_inv_a = (await db.execute(select(Invoice).where(Invoice.id == inv_a_id))).scalar_one()
        await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv_a)
        await db.commit()

    async with factory() as db:
        db_inv_b = (await db.execute(select(Invoice).where(Invoice.id == inv_b_id))).scalar_one()
        with pytest.raises(ValueError, match="already attached to another invoice"):
            await agreement_receipt_crud.claim(db, agr, [receipt.id], db_inv_b)

    async with factory() as db:
        fresh = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt.id)
        )).scalar_one()
    assert fresh.status == "reconciled"
    assert fresh.invoice_id == inv_a_id, "invoice B's rejected claim must not steal A's receipt"
