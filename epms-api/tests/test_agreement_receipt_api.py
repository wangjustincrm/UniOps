"""Agreement receipt endpoints: create, list, void, AP review.

⚠️ These tests exercise app/api/v1/agreement_slips.py — the router itself is
still named/routed/tagged in "slip" terms (URL segment `/slips`,
`AgreementPickupSlip`-aliased local names) pending Task 3's full rename; only
its internal attribute references were patched in Task 2 to keep it working
against the renamed schemas/crud/model (see app/crud/agreement_slip.py and
app/schemas/agreement_slip.py — both now thin compat shims onto
app/crud/agreement_receipt.py and app/schemas/agreement_receipt.py). The
REQUEST/RESPONSE bodies below use the new field names (receipt_date,
receipt_ref, received_by, missing_receipt_reason, receipt_type) because those
come from ReceiptCreate/ReceiptResponse, which Task 2 owns outright.
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


async def _create_agreement(admin_client, test_engine):
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(
        AGR_URL, json=_agr_payload(vendor_id, agreement_type="house_account"))).json()
    return created, user_id


def _receipts_url(agreement_id):
    # The router (app/api/v1/agreement_slips.py) still lives at .../slips —
    # renaming the URL segment itself is Task 3's job, not Task 2's.
    return f"{AGR_URL}/{agreement_id}/slips"


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
# (epms.agreement.slip.write), split out of epms.agreement.write so that
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

            # Grant the NEW key mid-test (mirrors what identity 0007_slip_write_perm
            # does in production for ap_clerk) — require_permission re-queries
            # role_permissions on every request (packages/authz/uniops_authz/core.py),
            # so no re-login / new token is needed for the grant to take effect.
            async with factory() as db:
                await db.execute(text(
                    "INSERT INTO permission_defs(key,module,label,sort) "
                    "VALUES ('epms.agreement.slip.write','epms','Record Agreement Receipts',106) "
                    "ON CONFLICT (key) DO NOTHING"))
                await db.execute(text(
                    "INSERT INTO role_permissions(role_code,permission_key) "
                    "VALUES ('ap_clerk','epms.agreement.slip.write') ON CONFLICT DO NOTHING"))
                await db.commit()

            r2 = await c.post(_receipts_url(agr["id"]), json=_receipt_payload(user_id, receipt_ref="POST-GRANT"))
            assert r2.status_code == 201, r2.text
    finally:
        async with factory() as db:
            await db.execute(text(
                "DELETE FROM role_permissions WHERE role_code = 'ap_clerk' "
                "AND permission_key IN ('epms.agreement.read', 'epms.agreement.slip.write')"))
            await db.commit()


async def test_dept_admin_can_reach_and_record_after_fix_round_1(admin_client, test_engine):
    """Fix-round 1 (Critical): the first cut of identity 0007_slip_write_perm
    granted dept_admin ONLY epms.agreement.slip.write. dept_admin was never in
    0006's epms.agreement.read grant set, so that alone left dept_admin unable
    to reach the page at all — no Agreements nav entry (Sidebar.tsx gates it
    on epms.agreement.read), GET /agreements/{id} 403s (AgrReadDep), GET
    .../slips 403s too. The fix adds an epms.agreement.read grant for
    dept_admin alongside the slip-write one. This test pins BOTH halves —
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
            "VALUES ('epms.agreement.slip.write','epms','Record Agreement Receipts',106) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('dept_admin','epms.agreement.read') ON CONFLICT DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('dept_admin','epms.agreement.slip.write') ON CONFLICT DO NOTHING"))
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
                "AND permission_key IN ('epms.agreement.read', 'epms.agreement.slip.write')"))
            await db.commit()


# ── Task 10 review round 2, Finding B: the house_account matching UI
# (MatchPanel) used to call GET /agreements/{id}/slips directly — gated on
# epms.agreement.read, a permission NOT granted by default to several roles
# that can legitimately match an invoice (its own uploader among them; also
# warehouse_staff / supervisor / cfo / vendor_manager / erp_pa_officer in
# production, per identity's 0006/0007 grant sets). Those callers 403'd on
# the receipt list and silently fell back to the no-evidence settlement path —
# exactly the deadlock list_agreement_candidates' own docstring warns about,
# one layer down. The fix is a new invoice-scoped route
# (GET /invoices/{id}/agreements/{agreement_id}/slips) authorised by the
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
        r = await uploader_client.get(f"/api/v1/invoices/{inv.id}/agreements/{agr.id}/slips")
        assert r.status_code == 200, r.text
        assert r.json() == {"items": [], "total": 0}


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
        r = await outsider_client.get(f"/api/v1/invoices/{inv['id']}/agreements/{agr.id}/slips")
        assert r.status_code == 403, r.text
    finally:
        await outsider_client.aclose()
