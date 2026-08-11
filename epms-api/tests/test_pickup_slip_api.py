"""House-account pickup slip endpoints: create, list, void, AP review."""
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement_slip import AgreementPickupSlip
from tests.test_agreements import AGR_URL, _agr_payload, seed_vendor_and_user

pytestmark = pytest.mark.asyncio


def _slip_payload(picked_by, **over):
    body = {
        "slip_date": "2026-08-01",
        "slip_ref": "1-510076",
        "amount": "100.00",
        "tax_amount": "13.00",
        "total_amount": "113.00",
        "picked_by": str(picked_by),
        "missing_slip_reason": None,
        "notes": None,
    }
    body.update(over)
    return body


async def _create_agreement(admin_client, test_engine):
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(
        AGR_URL, json=_agr_payload(vendor_id, agreement_type="house_account"))).json()
    return created, user_id


def _slips_url(agreement_id):
    return f"{AGR_URL}/{agreement_id}/slips"


async def test_create_slip_with_photo_lands_open(admin_client, test_engine):
    # 有附件的情形由 Task 3 覆盖;这里传 missing_slip_reason=None 且不带附件,
    # 仍应落 open —— 附件必填是前端规则,后端只在给了 missing_slip_reason 时
    # 才走 pending_ap_review。理由:后端无法可靠判断"用户是否打算传附件",
    # 把它做成后端强制会让"先建行再传附件"的两步上传流程无法进行。
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["missing_slip_reason"] is None
    assert body["invoice_id"] is None
    assert body["ap_reviewed_by"] is None


async def test_create_slip_with_missing_reason_lands_pending_ap_review(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r = await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, missing_slip_reason="Slip lost in transit"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_ap_review"
    assert body["missing_slip_reason"] == "Slip lost in transit"


async def test_ap_review_approve_moves_to_open_and_stamps_reviewer(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, missing_slip_reason="Slip lost"))).json()

    r = await admin_client.post(
        f"{_slips_url(agr['id'])}/{slip['id']}/ap-review", json={"action": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["ap_reviewed_by"] is not None
    assert body["ap_reviewed_at"] is not None


async def test_ap_review_reject_moves_to_rejected(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, missing_slip_reason="Slip lost"))).json()

    r = await admin_client.post(
        f"{_slips_url(agr['id'])}/{slip['id']}/ap-review", json={"action": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"


async def test_ap_review_on_an_open_slip_is_409(admin_client, test_engine):
    # 只有 pending_ap_review 可裁定
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()
    assert slip["status"] == "open"

    r = await admin_client.post(
        f"{_slips_url(agr['id'])}/{slip['id']}/ap-review", json={"action": "approve"})
    assert r.status_code == 409, r.text


async def test_void_open_slip_succeeds(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()

    r = await admin_client.delete(f"{_slips_url(agr['id'])}/{slip['id']}")
    assert r.status_code == 204, r.text

    listed = await admin_client.get(_slips_url(agr["id"]))
    voided = [s for s in listed.json()["items"] if s["id"] == slip["id"]][0]
    assert voided["status"] == "voided"


async def test_void_reconciled_slip_is_409(admin_client, test_engine):
    # 已认领的必须先释放 —— 直接作废会让发票挂着一张 voided 凭证
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == uuid.UUID(slip["id"])))).scalar_one()
        row.status = "reconciled"
        row.invoice_id = uuid.uuid4()
        await db.commit()

    r = await admin_client.delete(f"{_slips_url(agr['id'])}/{slip['id']}")
    assert r.status_code == 409, r.text


async def test_list_filters_by_status(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    open_slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, slip_ref="OPEN-1"))).json()
    pending_slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, slip_ref="PEND-1", missing_slip_reason="Slip lost"))).json()

    all_listed = await admin_client.get(_slips_url(agr["id"]))
    assert all_listed.status_code == 200
    ids = {s["id"] for s in all_listed.json()["items"]}
    assert {open_slip["id"], pending_slip["id"]} <= ids
    assert all_listed.json()["total"] >= 2

    filtered = await admin_client.get(_slips_url(agr["id"]), params={"status": "open"})
    assert filtered.status_code == 200
    filtered_ids = {s["id"] for s in filtered.json()["items"]}
    assert open_slip["id"] in filtered_ids
    assert pending_slip["id"] not in filtered_ids


async def test_slips_of_another_agreement_are_not_listed(admin_client, test_engine):
    agr1, user_id = await _create_agreement(admin_client, test_engine)
    agr2, _ = await _create_agreement(admin_client, test_engine)

    slip1 = (await admin_client.post(_slips_url(agr1["id"]), json=_slip_payload(
        user_id, slip_ref="AGR1-SLIP"))).json()
    await admin_client.post(_slips_url(agr2["id"]), json=_slip_payload(
        user_id, slip_ref="AGR2-SLIP"))

    listed = await admin_client.get(_slips_url(agr1["id"]))
    assert listed.status_code == 200
    ids = {s["id"] for s in listed.json()["items"]}
    assert slip1["id"] in ids
    assert len(listed.json()["items"]) == 1


# ── Review round 1, Critical #1: PATCH must not be reachable on a claimed or
# decided slip, and a partial amount edit must not leave the row internally
# inconsistent. ──────────────────────────────────────────────────────────

async def test_patch_reconciled_slip_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == uuid.UUID(slip["id"])))).scalar_one()
        row.status = "reconciled"
        row.invoice_id = uuid.uuid4()
        await db.commit()

    r = await admin_client.patch(
        f"{_slips_url(agr['id'])}/{slip['id']}", json={"notes": "edited after reconciliation"})
    assert r.status_code == 409, r.text


async def test_patch_rejected_slip_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, missing_slip_reason="Slip lost"))).json()
    rejected = (await admin_client.post(
        f"{_slips_url(agr['id'])}/{slip['id']}/ap-review", json={"action": "reject"})).json()
    assert rejected["status"] == "rejected"

    r = await admin_client.patch(
        f"{_slips_url(agr['id'])}/{slip['id']}", json={"notes": "edited after rejection"})
    assert r.status_code == 409, r.text


async def test_patch_amount_only_breaks_totals_is_rejected(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    # amount=100.00, tax_amount=13.00, total_amount=113.00 from _slip_payload
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()

    r = await admin_client.patch(
        f"{_slips_url(agr['id'])}/{slip['id']}", json={"amount": "150.00"})
    assert r.status_code == 409, r.text

    unchanged = await admin_client.get(_slips_url(agr["id"]), params={"status": "open"})
    row = [s for s in unchanged.json()["items"] if s["id"] == slip["id"]][0]
    assert row["amount"] == "100.00"   # patch must not have partially applied


async def test_patch_coherent_amounts_on_open_slip_succeeds(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    slip = (await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id))).json()

    r = await admin_client.patch(
        f"{_slips_url(agr['id'])}/{slip['id']}",
        json={"amount": "50.00", "tax_amount": "6.50", "total_amount": "56.50"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount"] == "50.00"
    assert body["tax_amount"] == "6.50"
    assert body["total_amount"] == "56.50"


# ── Review round 1, Important #2: the partial unique index on
# (agreement_id, slip_ref) is a real collision path (re-entry by a second
# person, or a client retry after a timeout) — it must surface as 409, not
# an unhandled 500. ──────────────────────────────────────────────────────

async def test_create_slip_duplicate_slip_ref_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    r1 = await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, slip_ref="DUP-REF-1"))
    assert r1.status_code == 201, r1.text

    r2 = await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(
        user_id, slip_ref="DUP-REF-1"))
    assert r2.status_code == 409, r2.text
    assert "DUP-REF-1" in r2.text


async def test_patch_duplicate_slip_ref_is_409(admin_client, test_engine):
    agr, user_id = await _create_agreement(admin_client, test_engine)
    await admin_client.post(_slips_url(agr["id"]), json=_slip_payload(user_id, slip_ref="TAKEN"))
    other = (await admin_client.post(
        _slips_url(agr["id"]), json=_slip_payload(user_id, slip_ref="FREE"))).json()

    r = await admin_client.patch(
        f"{_slips_url(agr['id'])}/{other['id']}", json={"slip_ref": "TAKEN"})
    assert r.status_code == 409, r.text
    assert "TAKEN" in r.text
