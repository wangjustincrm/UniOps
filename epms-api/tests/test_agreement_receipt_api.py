"""Agreement receipt endpoints: create, list, void, AP review.

These tests exercise app/api/v1/agreement_receipts.py, which Task 3 renamed
fully onto AgreementReceipt / ReceiptCreate / ReceiptResponse — router prefix,
tags, dependency names, and URL segment (`/receipts`) all speak in "receipt"
terms now. Task 4 finished the job: the write permission key is
`epms.agreement.receipt.write` (identity 0007_receipt_write_perm).
"""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.core.security import create_access_token
from app.main import create_app
from app.models.agreement_receipt import AgreementReceipt
from app.models.invoice import Invoice
from app.schemas.auth import RegisterRequest
from datetime import date
from decimal import Decimal
from httpx import ASGITransport, AsyncClient
from tests.test_agreement_invoice_match import _delegate_client, _make_active_agreement
from tests.test_agreements import AGR_URL, _agr_payload, seed_vendor_and_user

pytestmark = pytest.mark.asyncio


def _receipt_payload(received_by, **over):
    body = {
        "receipt_date": "2026-08-01",
        "receipt_ref": "1-510076",
        "amount": "100.00",
        "tax_amount": "13.00",
        "total_amount": "113.00",
        "received_by": str(received_by),
        "missing_receipt_reason": None,
        "notes": None,
    }
    body.update(over)
    return body


async def _create_agreement(admin_client, test_engine, **over):
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine)
    payload = _agr_payload(vendor_id, agreement_type="house_account", **over)
    created = (await admin_client.post(AGR_URL, json=payload)).json()
    return created, user_id


def _receipts_url(agreement_id):
    return f"{AGR_URL}/{agreement_id}/receipts"


async def test_create_receipt_with_photo_lands_open(admin_client, test_engine):
    # 有附件的情形由 Task 3 覆盖;这里传 missing_receipt_reason=None 且不带附件,
    # 仍应落 open —— 附件必填是前端规则,后端只在给了 missing_receipt_reason 时
    # 才走 pending_ap_review。理由:后端无法可靠判断"用户是否打算传附件",
    # 把它做成后端强制会让"先建行再传附件"的两步上传流程无法进行。
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["missing_receipt_reason"] is None
    assert body["invoice_id"] is None
    assert body["ap_reviewed_by"] is None
    assert body["receipt_type"] == "counter_slip"


async def test_create_receipt_with_missing_reason_lands_pending_ap_review(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, missing_receipt_reason="Receipt lost in transit"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_ap_review"
    assert body["missing_receipt_reason"] == "Receipt lost in transit"


async def test_create_receipt_defaults_receipt_type_to_counter_slip(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))
    assert r.status_code == 201, r.text
    assert r.json()["receipt_type"] == "counter_slip"


async def test_create_receipt_accepts_delivery_and_service_types(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    for i, receipt_type in enumerate(("delivery", "service")):
        r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
            user_id, receipt_type=receipt_type, receipt_ref=f"TYPE-{i}"))
        assert r.status_code == 201, r.text
        assert r.json()["receipt_type"] == receipt_type


async def test_create_rejects_an_unknown_receipt_type(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json={
        **_receipt_payload(user_id), "receipt_type": "carrier_pigeon"})
    assert r.status_code == 422, r.text
    assert "counter_slip" in r.text


async def test_ap_review_approve_moves_to_open_and_stamps_reviewer(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, missing_receipt_reason="Receipt lost"))).json()

    r = await admin_client.post(
        f"{_receipts_url(agr['id'])}/{receipt['id']}/ap-review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["ap_reviewed_by"] is not None
    assert body["ap_reviewed_at"] is not None


async def test_ap_review_reject_moves_to_rejected(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, missing_receipt_reason="Receipt lost"))).json()

    r = await admin_client.post(
        f"{_receipts_url(agr['id'])}/{receipt['id']}/ap-review", json={"action": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"


async def test_ap_review_on_an_open_receipt_is_409(admin_client, test_engine):
    # 只有 pending_ap_review 可裁定
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()
    assert receipt["status"] == "open"

    r = await admin_client.post(
        f"{_receipts_url(agr['id'])}/{receipt['id']}/ap-review", json={"action": "approve"})
    assert r.status_code == 409, r.text


async def test_void_open_receipt_succeeds(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    r = await admin_client.delete(f"{_receipts_url(agr['id'])}/{receipt['id']}")
    assert r.status_code == 204, r.text

    listed = await admin_client.get(_receipts_url(agr["id"]))
    voided = [s for s in listed.json()["items"] if s["id"] == receipt["id"]][0]
    assert voided["status"] == "voided"


async def test_void_reconciled_receipt_is_409(admin_client, test_engine):
    # 已认领的必须先释放 —— 直接作废会让发票挂着一份 voided 凭证
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == uuid.UUID(receipt["id"])))).scalar_one()
        row.status = "reconciled"
        row.invoice_id = uuid.uuid4()
        await db.commit()

    r = await admin_client.delete(f"{_receipts_url(agr['id'])}/{receipt['id']}")
    assert r.status_code == 409, r.text


async def test_list_filters_by_status(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    open_receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="OPEN-1"))).json()
    pending_receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="PEND-1", missing_receipt_reason="Receipt lost"))).json()

    all_listed = await admin_client.get(_receipts_url(agr["id"]))
    assert all_listed.status_code == 200
    ids = {s["id"] for s in all_listed.json()["items"]}
    assert {open_receipt["id"], pending_receipt["id"]} <= ids
    assert all_listed.json()["total"] >= 2

    filtered = await admin_client.get(_receipts_url(agr["id"]), params={"status": "open"})
    assert filtered.status_code == 200
    filtered_ids = {s["id"] for s in filtered.json()["items"]}
    assert open_receipt["id"] in filtered_ids
    assert pending_receipt["id"] not in filtered_ids


async def test_receipts_of_another_agreement_are_not_listed(admin_client, test_engine):
    agr1, user_id = await _create_agreement(admin_client, test_engine)
    agr2, _ = await _create_agreement(admin_client, test_engine)

    receipt1 = (await admin_client.post(_receipts_url(agr1["id"]), json=_receipt_payload(
        user_id, receipt_ref="AGR1-RECEIPT"))).json()
    await admin_client.post(_receipts_url(agr2["id"]), json=_receipt_payload(
        user_id, receipt_ref="AGR2-RECEIPT"))

    listed = await admin_client.get(_receipts_url(agr1["id"]))
    assert listed.status_code == 200
    ids = {s["id"] for s in listed.json()["items"]}
    assert receipt1["id"] in ids
    assert len(listed.json()["items"]) == 1


# ── Review round 1, Critical #1: PATCH must not be reachable on a claimed or
# decided receipt, and a partial amount edit must not leave the row internally
# inconsistent. ──────────────────────────────────────────────────────────

async def test_patch_reconciled_receipt_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == uuid.UUID(receipt["id"])))).scalar_one()
        row.status = "reconciled"
        row.invoice_id = uuid.uuid4()
        await db.commit()

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"notes": "edited after reconciliation"})
    assert r.status_code == 409, r.text


async def test_patch_rejected_receipt_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, missing_receipt_reason="Receipt lost"))).json()
    rejected = (await admin_client.post(
        f"{_receipts_url(agr['id'])}/{receipt['id']}/ap-review", json={"action": "reject"})).json()
    assert rejected["status"] == "rejected"

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"notes": "edited after rejection"})
    assert r.status_code == 409, r.text


async def test_patch_amount_only_breaks_totals_is_rejected(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    # amount=100.00, tax_amount=13.00, total_amount=113.00 from _receipt_payload
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"amount": "150.00"})
    assert r.status_code == 409, r.text

    unchanged = await admin_client.get(_receipts_url(agr["id"]), params={"status": "open"})
    row = [s for s in unchanged.json()["items"] if s["id"] == receipt["id"]][0]
    assert row["amount"] == "100.00"   # patch must not have partially applied


async def test_patch_coherent_amounts_on_open_receipt_succeeds(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}",
        json={"amount": "50.00", "tax_amount": "6.50", "total_amount": "56.50"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount"] == "50.00"
    assert body["tax_amount"] == "6.50"
    assert body["total_amount"] == "56.50"


async def test_patch_receipt_type_to_an_unknown_value_is_422(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"receipt_type": "carrier_pigeon"})
    assert r.status_code == 422, r.text
    assert "counter_slip" in r.text


# ── Review round 1, Important #2: the partial unique index on
# (agreement_id, receipt_ref) is a real collision path (re-entry by a second
# person, or a client retry after a timeout) — it must surface as 409, not
# an unhandled 500. ──────────────────────────────────────────────────────

async def test_create_receipt_duplicate_receipt_ref_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r1 = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="DUP-REF-1"))
    assert r1.status_code == 201, r1.text

    r2 = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="DUP-REF-1"))
    assert r2.status_code == 409, r2.text
    assert "DUP-REF-1" in r2.text
    # I3: the 409 must name the row that actually holds the ref and its status —
    # otherwise the recorder can't tell whether the blocker is something they
    # can void, and (before this fix) any OTHER IntegrityError wore the same
    # message.
    assert r1.json()["id"] in r2.text
    assert "open" in r2.text


# ── Whole-branch review I3: void / AP-reject must RELEASE the receipt_ref.
# Both are documented, ordinary paths (design §3 "open ──录错作废──→ voided";
# AP reject is the entire point of the pending_ap_review gate), and both used
# to burn the ref inside that agreement permanently — re-recording the same
# paper receipt with the corrected amount was a dead end with no UI able to
# free it. These three tests are the behaviour, not the DDL: the test DB is
# built by Base.metadata.create_all, so they only pass if the MODEL's
# partial-index predicate changed, not just the migration. ────────────────

async def test_voided_receipt_releases_its_ref_for_re_entry(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    wrong = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510076", amount="100.00", tax_amount="13.00",
        total_amount="113.00"))).json()

    voided = await admin_client.delete(f"{_receipts_url(agr['id'])}/{wrong['id']}")
    assert voided.status_code == 204, voided.text

    # Same paper receipt, corrected amount.
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510076", amount="200.00", tax_amount="26.00",
        total_amount="226.00"))
    assert r.status_code == 201, r.text
    assert r.json()["id"] != wrong["id"]
    assert r.json()["status"] == "open"


async def test_ap_rejected_receipt_releases_its_ref_for_re_entry(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510077", missing_receipt_reason="Receipt lost in transit"))).json()
    rejected = await admin_client.post(
        f"{_receipts_url(agr['id'])}/{receipt['id']}/ap-review", json={"action": "reject"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected", rejected.text

    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510077"))
    assert r.status_code == 201, r.text
    assert r.json()["id"] != receipt["id"]


async def test_two_live_receipts_still_cannot_share_a_ref(admin_client, test_engine):
    # The narrowing must NOT weaken the constraint where it earns its keep:
    # two live rows claiming one paper receipt is still the collision the
    # index was added for.
    agr, user_id = await _create_agreement(admin_client, test_engine)
    first = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510078"))).json()
    pending = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="1-510078", missing_receipt_reason="Receipt lost"))
    assert pending.status_code == 409, pending.text
    assert first["id"] in pending.text


async def test_patch_duplicate_receipt_ref_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="TAKEN"))
    other = (await admin_client.post(
        _receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="FREE"))).json()

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{other['id']}", json={"receipt_ref": "TAKEN"})
    assert r.status_code == 409, r.text
    assert "TAKEN" in r.text


# ── Review round 3: update() must apply the SAME open/pending_ap_review
# routing rule create() always has — PATCHing a reason onto an open receipt
# was a hole in the evidence chain (a receipt claiming "no evidence" that was
# never sent to AP). One-directional: clearing the reason must NOT flip a
# pending_ap_review receipt back to open (that's ap_review()'s call, not an
# editor's), and re-patching an already-pending_ap_review receipt's reason
# must be a no-op, not an error. ──────────────────────────────────────────

async def test_patch_adding_missing_reason_to_open_receipt_routes_to_ap_review(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()
    assert receipt["status"] == "open"

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}",
        json={"missing_receipt_reason": "Photo upload failed after receipt was recorded"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_ap_review"
    assert body["missing_receipt_reason"] == "Photo upload failed after receipt was recorded"


async def test_patch_clearing_reason_on_open_receipt_stays_open(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id))).json()
    assert receipt["status"] == "open"

    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"missing_receipt_reason": None})
    assert r.status_code == 200, r.text
    body = r.json()
    # An empty/absent reason must never itself trigger the AP gate — status
    # was already open and stays open (this is NOT the reverse-transition
    # case; that's covered below against a receipt that starts
    # pending_ap_review).
    assert body["status"] == "open"
    assert body["missing_receipt_reason"] is None


async def test_patch_reason_on_pending_ap_review_receipt_stays_pending_and_does_not_error(
    admin_client, test_engine,
):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, missing_receipt_reason="Receipt lost in transit"))).json()
    assert receipt["status"] == "pending_ap_review"

    # Re-wording the reason (still non-empty) must be idempotent — no crash,
    # no re-triggering anything, status untouched.
    r = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}",
        json={"missing_receipt_reason": "Receipt lost in transit — confirmed with vendor"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_ap_review"
    assert body["missing_receipt_reason"] == "Receipt lost in transit — confirmed with vendor"

    # Clearing the reason on an already-pending receipt must NOT silently pull
    # it back to open — that would let an editor revoke AP's gate by
    # blanking a text field. Reverting the AP decision is ap_review()'s job.
    r2 = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"missing_receipt_reason": None})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["status"] == "pending_ap_review"
    assert body2["missing_receipt_reason"] is None


# ── Task 9b: receipt recording is its own permission key
# (epms.agreement.receipt.write), split out of epms.agreement.write so that
# someone who can record a receipt cannot thereby edit the agreement's own
# terms. Every test above runs as admin_client (system_admin), which
# uniops_authz short-circuits past ANY permission check (see
# packages/authz/uniops_authz/core.py's `if role == "system_admin"`) — none
# of them can tell a correct key from a wrong (or missing) one. These two
# tests are the ones that can.
#
# Both grant rows are cleaned up in a `finally` block: role_defs/
# permission_defs/role_permissions are SESSION-level tables (created once,
# not per-test) and conftest.py's _restore_default_matrix only ever
# INSERT ... ON CONFLICT DO NOTHING — it never deletes a row that isn't in
# the default matrix. Without the cleanup, a grant made here would silently
# outlive this test and leak into every later test in the same pytest
# session, e.g. quietly making a future "ap_clerk/dept_admin can't do X"
# test pass for the wrong reason if that file happens to run afterward. ───

async def test_non_admin_without_receipt_write_grant_is_403_then_201_once_granted(
    admin_client, test_engine,
):
    agr, user_id = await _create_agreement(admin_client, test_engine)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # ap_clerk holds epms.agreement.read in production (identity
        # 0006_agreement_perms) — seed the same grant here so this caller is
        # a realistic "can view the agreement, cannot record a receipt" user,
        # not merely a role with nothing seeded at all. conftest's default
        # matrix only reproduces the 17 phase-1 keys, so phase-2 grants like
        # this one must be seeded per-test (same pattern as
        # test_gr_create_authz.py's _grant_gr_receive).
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('ap_clerk','epms.agreement.read') ON CONFLICT DO NOTHING"))
        await db.commit()

    try:
        from tests.conftest import _authenticated_client
        async with await _authenticated_client(test_engine, "ap_clerk") as c:
            r = await c.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="PRE-GRANT"))
            assert r.status_code == 403, r.text

            # Grant the NEW key mid-test (mirrors what identity 0007_receipt_write_perm
            # does in production for ap_clerk) — require_permission re-queries
            # role_permissions on every request (packages/authz/uniops_authz/core.py),
            # so no re-login / new token is needed for the grant to take effect.
            async with factory() as db:
                await db.execute(text(
                    "INSERT INTO permission_defs(key,module,label,sort) "
                    "VALUES ('epms.agreement.receipt.write','epms','Record Agreement Receipts',106) "
                    "ON CONFLICT (key) DO NOTHING"))
                await db.execute(text(
                    "INSERT INTO role_permissions(role_code,permission_key) "
                    "VALUES ('ap_clerk','epms.agreement.receipt.write') ON CONFLICT DO NOTHING"))
                await db.commit()

            r2 = await c.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="POST-GRANT"))
            assert r2.status_code == 201, r2.text
    finally:
        async with factory() as db:
            await db.execute(text(
                "DELETE FROM role_permissions WHERE role_code = 'ap_clerk' "
                "AND permission_key IN ('epms.agreement.read', 'epms.agreement.receipt.write')"))
            await db.commit()


async def test_dept_admin_can_reach_and_record_after_fix_round_1(admin_client, test_engine):
    """Fix-round 1 (Critical): the first cut of identity 0007_receipt_write_perm
    granted dept_admin ONLY epms.agreement.receipt.write. dept_admin was never
    in 0006's epms.agreement.read grant set, so that alone left dept_admin
    unable to reach the page at all — no Agreements nav entry (Sidebar.tsx
    gates it on epms.agreement.read), GET /agreements/{id} 403s (AgrReadDep),
    GET .../receipts 403s too. The fix adds an epms.agreement.read grant for
    dept_admin alongside the receipt-write one. This test pins BOTH halves —
    read reachability AND the actual record action — so a future edit that
    drops either one fails loudly instead of shipping a permission that
    looks granted in the matrix but does nothing.
    """
    agr, user_id = await _create_agreement(admin_client, test_engine)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.receipt.write','epms','Record Agreement Receipts',106) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('dept_admin','epms.agreement.read') ON CONFLICT DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('dept_admin','epms.agreement.receipt.write') ON CONFLICT DO NOTHING"))
        await db.commit()

    try:
        from tests.conftest import _authenticated_client
        async with await _authenticated_client(test_engine, "dept_admin") as c:
            r_get = await c.get(f"{AGR_URL}/{agr['id']}")
            assert r_get.status_code == 200, r_get.text

            r_list = await c.get(_receipts_url(agr["id"]))
            assert r_list.status_code == 200, r_list.text

            r_post = await c.post(
                _receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="DEPT-ADMIN-1"))
            assert r_post.status_code == 201, r_post.text
    finally:
        async with factory() as db:
            await db.execute(text(
                "DELETE FROM role_permissions WHERE role_code = 'dept_admin' "
                "AND permission_key IN ('epms.agreement.read', 'epms.agreement.receipt.write')"))
            await db.commit()


# ── Task 10 review round 2, Finding B: the house_account matching UI
# (MatchPanel) used to call GET /agreements/{id}/receipts directly — gated on
# epms.agreement.read, a permission NOT granted by default to several roles
# that can legitimately match an invoice (its own uploader among them; also
# warehouse_staff / supervisor / cfo / vendor_manager / erp_pa_officer in
# production, per identity's 0006/0007 grant sets). Those callers 403'd on
# the receipt list and silently fell back to the no-evidence settlement path —
# exactly the deadlock list_agreement_candidates' own docstring warns about,
# one layer down. The fix is a new invoice-scoped route
# (GET /invoices/{id}/agreements/{agreement_id}/receipts) authorised by the
# SAME shared helper (_require_invoice_match_access) list_match_candidates
# and list_agreement_candidates already used — not a parallel copy.
#
# Both tests below deliberately use a NON-admin caller: admin_client is
# system_admin, which uniops_authz short-circuits past every permission
# check — it cannot tell a correctly-scoped gate from a missing one. ───────

async def test_invoice_scoped_receipt_list_reachable_by_uploader_without_agreement_read(
    admin_client, test_engine,
):
    """The invoice's own uploader — a role holding NEITHER
    epms.agreement.read (nobody has it by default in this test matrix; it's
    a phase-2 grant, see the block comment above
    test_non_admin_without_receipt_write_grant_is_403_then_201_once_granted)
    NOR system_admin's authz bypass — must still reach the receipt list for
    one of this invoice's candidate agreements. This is the caller shape that
    actually exercises the fix: is_uploader, not the _AP_ROLES branch of
    _require_invoice_match_access.
    """
    # _make_active_agreement, NOT the plain-POST _create_agreement helper
    # above: a freshly-POSTed agreement starts in "draft" (no approval-api
    # running in this suite to move it to "active"), and
    # candidates_for_vendor / _admissible_predicate only admit "active" or
    # "expired"-within-grace — a draft agreement 404s here via the
    # membership check below exactly like a foreign-vendor one would, which
    # would test the wrong thing entirely.
    vendor_id, vendor_name, seed_user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, seed_user_id)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        uploader = await user_crud.create(db, RegisterRequest(
            email=f"uploader-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name="Uploader Tester", role="warehouse_staff"))
        await db.commit()
        await db.refresh(uploader)

        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number=f"STMT-{uuid.uuid4().hex[:6]}",
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=Decimal("100.00"), tax_amount=Decimal("0.00"), total_amount=Decimal("100.00"),
            invoice_date=date(2026, 7, 31), due_date=date(2026, 8, 30),
            uploaded_by=uploader.id, line_items=[],
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

    token = create_access_token(str(uploader.id), uploader.role)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as uploader_client:
        r = await uploader_client.get(f"/api/v1/invoices/{inv.id}/agreements/{agr.id}/receipts")
        assert r.status_code == 200, r.text
        assert r.json() == {"items": [], "total": 0}


# ── Task 9: cross-agreement receipt list (GET /agreement-receipts) ─────────
# Backs its own menu entry so AP/warehouse staff can find or record a receipt
# without opening an agreement first (see task-9 brief's "where this fits").
# Separate endpoint from GET /agreements/{id}/receipts above — same read
# permission (epms.agreement.read), but no agreement_id in the URL, so every
# item must carry the parent agreement's NUMBER (never a bare UUID — brief
# item 5).

ALL_RECEIPTS_URL = "/api/v1/agreement-receipts"


async def test_list_all_receipts_across_agreements(admin_client, test_engine):
    """Two different agreements, one receipt each → with no filters, both
    show up, and each carries its OWN parent agreement's number."""
    agr1, user_id = await _create_agreement(admin_client, test_engine)
    agr2, _ = await _create_agreement(admin_client, test_engine)
    r1 = (await admin_client.post(_receipts_url(agr1["id"]), json=_receipt_payload(
        user_id, receipt_ref="CROSS-1"))).json()
    r2 = (await admin_client.post(_receipts_url(agr2["id"]), json=_receipt_payload(
        user_id, receipt_ref="CROSS-2"))).json()

    # page_size=200 (the documented cap) explicitly — this test file runs many
    # earlier tests against the SAME session-scoped test_engine with no
    # per-test rollback, all seeding receipts with the same 2026-08-01
    # receipt_date as _receipt_payload's default. The server default
    # page_size=20 would silently truncate before these two rows and make
    # this assertion flaky on suite order, not on the endpoint's behaviour.
    listed = await admin_client.get(ALL_RECEIPTS_URL, params={"page_size": 200})
    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert {r1["id"], r2["id"]} <= by_id.keys()
    assert by_id[r1["id"]]["agreement_number"] == agr1["number"]
    assert by_id[r2["id"]]["agreement_number"] == agr2["number"]


# ── Fix round 1, Critical: this listing spans MULTIPLE agreements, which can
# each be a different currency (AgreementCreatePage's currency dropdown is a
# real user choice, not decorative). A row must carry its OWN parent
# agreement's currency, not a hardcoded one — pins the exact bug the review
# caught: a USD house account's receipt rendering as CA$ on this page while
# the same row shows US$ on the agreement detail page. ──────────────────────

async def test_list_all_reports_each_row_own_agreement_currency(admin_client, test_engine):
    agr_cad, user_id = await _create_agreement(admin_client, test_engine, currency="CAD")
    agr_usd, _ = await _create_agreement(admin_client, test_engine, currency="USD")
    r_cad = (await admin_client.post(_receipts_url(agr_cad["id"]), json=_receipt_payload(
        user_id, receipt_ref="CUR-CAD-1"))).json()
    r_usd = (await admin_client.post(_receipts_url(agr_usd["id"]), json=_receipt_payload(
        user_id, receipt_ref="CUR-USD-1"))).json()

    listed = await admin_client.get(ALL_RECEIPTS_URL, params={"page_size": 200})
    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[r_cad["id"]]["currency"] == "CAD"
    assert by_id[r_usd["id"]]["currency"] == "USD"


# ── Fix round 1, Important 2: the linked-invoice column must never render a
# bare invoice_id UUID — it must carry the invoice's human internal_ref,
# resolved server-side via a LEFT join (most rows have no invoice at all). ──

async def test_list_all_reports_linked_invoice_ref(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    vendor_id = uuid.UUID(agr["vendor_id"])
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="INV-REF-1"))).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number=f"STMT-{uuid.uuid4().hex[:6]}",
            vendor_id=vendor_id, vendor_name=agr["vendor_name"],
            amount=Decimal("100.00"), tax_amount=Decimal("13.00"), total_amount=Decimal("113.00"),
            invoice_date=date(2026, 7, 31), due_date=date(2026, 8, 30),
            uploaded_by=user_id, line_items=[],
        )
        db.add(inv)
        await db.flush()
        row = (await db.execute(select(AgreementReceipt).where(
            AgreementReceipt.id == uuid.UUID(receipt["id"])))).scalar_one()
        row.status = "reconciled"
        row.invoice_id = inv.id
        await db.commit()
        inv_id, inv_ref = inv.id, inv.internal_ref

    listed = await admin_client.get(
        ALL_RECEIPTS_URL, params={"agreement_id": agr["id"], "page_size": 200})
    assert listed.status_code == 200, listed.text
    row = [item for item in listed.json()["items"] if item["id"] == receipt["id"]][0]
    assert row["invoice_id"] == str(inv_id)
    assert row["invoice_ref"] == inv_ref

    # A receipt with no invoice at all — the common case — must report
    # invoice_ref as null, not error or fabricate a value (LEFT join, not INNER).
    other = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="INV-REF-NONE"))).json()
    listed2 = await admin_client.get(
        ALL_RECEIPTS_URL, params={"agreement_id": agr["id"], "page_size": 200})
    row2 = [item for item in listed2.json()["items"] if item["id"] == other["id"]][0]
    assert row2["invoice_id"] is None
    assert row2["invoice_ref"] is None


# ── Fix round 1, Important 1: receipt_date alone is not a unique sort key —
# a busy house account posts many receipts on one calendar date, and Postgres
# gives no ordering guarantee among tied rows across two separate queries
# (this endpoint's COUNT and its SELECT are two queries). Paging over ties
# with no full tiebreaker chain can duplicate a row onto two pages and skip
# another — this test catches that directly by paging page_size=1 through
# same-day rows and checking the union is exactly the created set with no
# repeats. ────────────────────────────────────────────────────────────────

async def test_list_all_pagination_has_no_duplicates_or_gaps_on_tied_dates(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    created_ids = set()
    for i in range(5):
        r = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
            user_id, receipt_ref=f"TIEBREAK-{i}"))).json()
        created_ids.add(r["id"])

    seen_ids: list[str] = []
    for page in range(1, 6):
        r = await admin_client.get(ALL_RECEIPTS_URL, params={
            "agreement_id": agr["id"], "page": page, "page_size": 1})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1, r.text
        seen_ids.append(items[0]["id"])

    assert len(seen_ids) == len(set(seen_ids)), "a row was returned on more than one page"
    assert set(seen_ids) == created_ids, "paging skipped or fabricated a row"


async def test_list_all_filters_by_receipt_type(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    counter = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="TYPE-FILTER-1"))).json()
    service = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="TYPE-FILTER-2", receipt_type="service"))).json()

    # Scoped to THIS test's own fresh agreement (via agreement_id) as well as
    # receipt_type — with only two rows ever posted under it, this can never
    # be truncated by the server's default page_size regardless of how many
    # OTHER receipts earlier tests in this session-scoped suite have created.
    r = await admin_client.get(
        ALL_RECEIPTS_URL, params={"receipt_type": "service", "agreement_id": agr["id"]})
    assert r.status_code == 200, r.text
    ids = {item["id"] for item in r.json()["items"]}
    assert service["id"] in ids
    assert counter["id"] not in ids


async def test_list_all_filters_by_agreement(admin_client, test_engine):
    agr1, user_id = await _create_agreement(admin_client, test_engine)
    agr2, _ = await _create_agreement(admin_client, test_engine)
    r1 = (await admin_client.post(_receipts_url(agr1["id"]), json=_receipt_payload(
        user_id, receipt_ref="AGR-FILTER-1"))).json()
    await admin_client.post(_receipts_url(agr2["id"]), json=_receipt_payload(
        user_id, receipt_ref="AGR-FILTER-2"))

    r = await admin_client.get(ALL_RECEIPTS_URL, params={"agreement_id": agr1["id"]})
    assert r.status_code == 200, r.text
    ids = {item["id"] for item in r.json()["items"]}
    assert ids == {r1["id"]}


async def test_list_all_requires_agreement_read(requester_client):
    """★ Deliberately requester_client, NOT admin_client. admin_client is
    system_admin, and uniops_authz short-circuits system_admin past every
    permission check — a test written against it would pass even if this
    route required the wrong key, or no key at all. epms.agreement.read is a
    phase-2 permission that conftest's default matrix grants to NO role
    (see the block comment above test_non_admin_without_receipt_write_grant_
    is_403_then_201_once_granted), so a plain requester without any extra
    grant is a real 403 case, not a coincidence of test setup.
    """
    r = await requester_client.get(ALL_RECEIPTS_URL)
    assert r.status_code == 403, r.text


async def test_list_all_reachable_once_agreement_read_is_granted(admin_client, test_engine):
    """Fix round 1, Minor 1: the 403 test above only proves a gate exists —
    conftest's default matrix rejects EVERY role for EVERY unrecognised key,
    so it can't tell a correctly-keyed gate (epms.agreement.read) from one
    guarding a typo'd key that nothing will ever hold. Granting the REAL key
    to a real non-admin role and asserting 200 is the half that can actually
    fail if the key were wrong.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('ap_clerk','epms.agreement.read') ON CONFLICT DO NOTHING"))
        await db.commit()

    try:
        from tests.conftest import _authenticated_client
        async with await _authenticated_client(test_engine, "ap_clerk") as c:
            r = await c.get(ALL_RECEIPTS_URL)
            assert r.status_code == 200, r.text
    finally:
        async with factory() as db:
            await db.execute(text(
                "DELETE FROM role_permissions WHERE role_code = 'ap_clerk' "
                "AND permission_key = 'epms.agreement.read'"))
            await db.commit()


async def test_invoice_scoped_receipt_list_403s_for_unrelated_caller(admin_client, test_engine):
    """Constraint 2 (task instructions): widening this route must NOT become
    'any authenticated user reads any agreement's receipts'. A real,
    authenticated caller with no relationship to this invoice at all — not
    AP staff, not its uploader, no open match task — must still be refused.
    """
    vendor_id, vendor_name, seed_user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, seed_user_id)
    inv = (await admin_client.post("/api/v1/invoices", json={
        "vendor_id": str(vendor_id),
        "vendor_invoice_number": f"STMT-{uuid.uuid4().hex[:6]}",
        "amount": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-07-31", "due_date": "2026-08-30",
        "line_items": [{"description": "Monthly statement", "quantity": "1",
                        "unit_price": "100.00", "line_total": "100.00"}],
    })).json()

    outsider_client, _outsider_id = await _delegate_client(test_engine)
    try:
        r = await outsider_client.get(f"/api/v1/invoices/{inv['id']}/agreements/{agr.id}/receipts")
        assert r.status_code == 403, r.text
    finally:
        await outsider_client.aclose()


# ── Task 12: single-receipt read (GET /agreement-receipts/{receipt_id}) ────
# ReceiptDetailPage's URL is /receipts/{receipt_id} — no agreement in it — so
# it cannot use the agreement-scoped read. Same router, same permission
# (epms.agreement.read), same response shape as a listing row so the page and
# the list speak one language.

def _one_receipt_url(receipt_id):
    return f"{ALL_RECEIPTS_URL}/{receipt_id}"


async def test_get_one_receipt_carries_agreement_currency_and_attachment_count(
    admin_client, test_engine,
):
    """① Detail read returns the SAME enriched shape as a listing row.

    Currency comes from the parent agreement (USD here, deliberately not the
    CAD default) because the page renders amounts with it — a hardcoded CAD
    was a Critical on this branch once already. attachment_count is what the
    page's "no photo" warning turns on, so it is asserted against a receipt
    that really has one attachment on it, not against 0 (which a query that
    forgot the subquery could also produce).
    """
    agr, user_id = await _create_agreement(admin_client, test_engine, currency="USD")
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="DETAIL-1"))).json()
    # Attachment row written directly rather than through
    # POST .../attachments: that route calls out to the file-api sidecar,
    # which test_agreement_receipt_attachments.py fakes with a module-autouse
    # fixture this file doesn't have (and the real sidecar 401s on this
    # suite's JWT_SECRET_KEY — see that file's _FakeFileServer docstring).
    # What is under test here is the COUNT the detail read reports, which
    # comes from the attachments table, not from how the row got there.
    from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(AgreementReceiptAttachment(
            receipt_id=uuid.UUID(receipt["id"]), filename="slip.jpg",
            content_type="image/jpeg", file_size=11, storage_key=uuid.uuid4()))
        await db.commit()

    r = await admin_client.get(_one_receipt_url(receipt["id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == receipt["id"]
    assert body["agreement_id"] == agr["id"]
    assert body["agreement_number"] == agr["number"]
    assert body["currency"] == "USD"
    assert body["attachment_count"] == 1
    assert body["invoice_ref"] is None
    # The editable fields the detail page's form round-trips must all be here.
    for field in ("receipt_type", "receipt_date", "receipt_ref", "amount", "tax_amount",
                  "total_amount", "received_by", "missing_receipt_reason", "notes", "status"):
        assert field in body, field


async def test_get_one_receipt_unknown_id_is_404(admin_client):
    """② A receipt id that doesn't exist is a 404 that names the RECEIPT —
    with no agreement in this URL, a bare "Not found" can't say which of the
    two the caller got wrong."""
    r = await admin_client.get(_one_receipt_url(uuid.uuid4()))
    assert r.status_code == 404, r.text
    assert "receipt" in r.json()["detail"].lower()


async def test_get_one_receipt_requires_agreement_read(requester_client):
    """③ ★ Deliberately requester_client, NOT admin_client: admin_client is
    system_admin and uniops_authz short-circuits it past EVERY permission
    check, so a test written against it would pass even if this route carried
    no gate at all. epms.agreement.read is granted to no role in conftest's
    default matrix (see the block comment above
    test_non_admin_without_receipt_write_grant_is_403_then_201_once_granted),
    so this is a real 403 rather than an artefact of test setup.
    """
    r = await requester_client.get(_one_receipt_url(uuid.uuid4()))
    assert r.status_code == 403, r.text
    # 403 BEFORE 404: the permission gate must not be reachable-around by
    # probing ids (an unauthorised caller learning which receipt ids exist).


async def test_list_all_is_not_shadowed_by_the_detail_route(admin_client, test_engine):
    """④ `/agreement-receipts` (list) and `/agreement-receipts/{receipt_id}`
    (detail) live on the same router and same prefix, and FastAPI matches in
    declaration order. This pins that adding the parametrised route did not
    swallow the list — the failure mode would be a 422 ("receipt_id is not a
    valid UUID") or a 404 on a URL that used to return a page of rows.
    """
    agr, user_id = await _create_agreement(admin_client, test_engine)
    created = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="SHADOW-1"))).json()

    listed = await admin_client.get(ALL_RECEIPTS_URL, params={"agreement_id": agr["id"]})
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
    # And with query params stripped entirely — the exact URL the frontend's
    # unfiltered first load requests.
    bare = await admin_client.get(ALL_RECEIPTS_URL)
    assert bare.status_code == 200, bare.text
    assert isinstance(bare.json()["items"], list)


async def test_patched_receipt_is_visible_through_the_detail_read(admin_client, test_engine):
    """The detail page edits via PATCH /agreements/{a}/receipts/{r} and then
    re-reads through THIS endpoint. Both must speak about the same row —
    pinned here because the two live on different routers with different
    scoping rules, which is exactly the kind of split that drifts.
    """
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="DETAIL-PATCH-1"))).json()

    patched = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}",
        json={"receipt_type": "delivery", "amount": "200.00",
              "tax_amount": "26.00", "total_amount": "226.00", "notes": "edited"},
    )
    assert patched.status_code == 200, patched.text

    r = await admin_client.get(_one_receipt_url(receipt["id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receipt_type"] == "delivery"
    assert Decimal(body["total_amount"]) == Decimal("226.00")
    assert body["notes"] == "edited"


# ── Task 13: the merchant printed on the receipt, and the mismatch reminder ──
# The agreement already carries the vendor it is WITH; what it cannot show is
# the merchant the paper slip was issued BY. Storing that and comparing the
# two is what surfaces the classic house-account mis-posting: shop A's slip
# recorded against shop B's account. The verdict is derived server-side so the
# list page and the detail page can never disagree about it.
#
# 判定函数本身的单元覆盖在 tests/test_receipt_vendor_match.py —— 它是同步的
# 纯函数测试,而本文件顶部的 `pytestmark = pytest.mark.asyncio` 会作用到模块里
# 每一个函数;同步函数被这个 mark 命中后,pytest-asyncio 会另起一个事件循环,
# 而 session 作用域的 test_engine 还绑在原来那个上,结果是它后面的每一条
# 异步用例都炸 "attached to a different loop"(实测 7 条)。

async def test_create_and_read_back_the_vendor_on_the_receipt(admin_client, test_engine):
    """① 录入时存下的商家名要能原样读回 —— 包括 store number 这类原文细节。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-1", vendor_name="PRINCESS AUTO #12"))
    assert r.status_code == 201, r.text
    assert r.json()["vendor_name"] == "PRINCESS AUTO #12"

    detail = await admin_client.get(_one_receipt_url(r.json()["id"]))
    assert detail.status_code == 200, detail.text
    assert detail.json()["vendor_name"] == "PRINCESS AUTO #12"


async def test_vendor_is_optional_on_create(admin_client, test_engine):
    """② 不传就是 NULL,且**不算**不一致 —— 抽不到抬头不该被当成错。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-NONE-1"))
    assert r.status_code == 201, r.text
    assert r.json()["vendor_name"] is None

    detail = (await admin_client.get(_one_receipt_url(r.json()["id"]))).json()
    assert detail["vendor_mismatch"] is False


async def test_detail_read_reports_a_mismatch_and_names_both_sides(admin_client, test_engine):
    """③ 判定在后端。前端要能写出"小票上印的是 X,协议的供应商是 Y",
    所以协议的供应商必须跟着这一行回来 —— 只给一个布尔,警告无法落地。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-MISMATCH-1", vendor_name="Canadian Tire #241"))).json()

    body = (await admin_client.get(_one_receipt_url(receipt["id"]))).json()
    assert body["vendor_mismatch"] is True
    assert body["vendor_name"] == "Canadian Tire #241"
    # seed_vendor_and_user 默认建的供应商就叫 Princess Auto。
    assert body["agreement_vendor_name"] == "Princess Auto"


async def test_a_store_number_variant_is_not_reported_as_a_mismatch(admin_client, test_engine):
    """④ 端到端地钉住宽容口径 —— 单元测试证明函数宽容,这条证明**端点用的是它**。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-TOLERANT-1", vendor_name="PRINCESS AUTO #12"))).json()

    body = (await admin_client.get(_one_receipt_url(receipt["id"]))).json()
    assert body["vendor_mismatch"] is False


async def test_list_all_carries_the_same_verdict_as_the_detail_read(admin_client, test_engine):
    """⑤ 列表页和详情页读同一份 JSON —— 两处判定必须逐字一致,
    否则用户在一处看到警告、在另一处看不到。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    bad = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-LIST-BAD", vendor_name="Canadian Tire"))).json()
    ok = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-LIST-OK", vendor_name="Princess Auto Ltd"))).json()

    listed = await admin_client.get(
        ALL_RECEIPTS_URL, params={"agreement_id": agr["id"], "page_size": 200})
    assert listed.status_code == 200, listed.text
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[bad["id"]]["vendor_mismatch"] is True
    assert by_id[bad["id"]]["agreement_vendor_name"] == "Princess Auto"
    assert by_id[ok["id"]]["vendor_mismatch"] is False
    for receipt_id in (bad["id"], ok["id"]):
        detail = (await admin_client.get(_one_receipt_url(receipt_id))).json()
        assert detail["vendor_mismatch"] == by_id[receipt_id]["vendor_mismatch"]


async def test_patching_the_vendor_clears_the_mismatch(admin_client, test_engine):
    """⑥ 商家名可改(与其它 OCR 字段一致),改对之后提醒必须跟着消失 ——
    否则这个警告就成了洗不掉的污点,下次没人再理它。"""
    agr, user_id = await _create_agreement(admin_client, test_engine)
    receipt = (await admin_client.post(_receipts_url(agr["id"]), json=_receipt_payload(
        user_id, receipt_ref="VENDOR-PATCH-1", vendor_name="Canadain Tire"))).json()
    assert (await admin_client.get(_one_receipt_url(receipt["id"]))).json()["vendor_mismatch"] is True

    patched = await admin_client.patch(
        f"{_receipts_url(agr['id'])}/{receipt['id']}", json={"vendor_name": "Princess Auto #12"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["vendor_name"] == "Princess Auto #12"

    body = (await admin_client.get(_one_receipt_url(receipt["id"]))).json()
    assert body["vendor_mismatch"] is False
