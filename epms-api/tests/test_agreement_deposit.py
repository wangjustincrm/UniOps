"""OneTime (Deposit) agreements — deposit PA without invoice, refund ≤ deposit
(user decisions 2026-11-17)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement_attachment import AgreementAttachment
from app.models.pa import PaymentApplication
from app.models.task import Task
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreement_pa import (  # noqa: F401 — autouse fixture re-exported
    _client_with_user,
    _ensure_approval_dept_routing_table,
)
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

PA_URL = "/api/v1/pa"
AGR_URL = "/api/v1/agreements"


@pytest.fixture(autouse=True)
def fake_upload(monkeypatch):
    stored: list[tuple[str, int]] = []

    async def _upload(data, filename, content_type, doc_type, doc_id, token):
        stored.append((filename, len(data)))
        return uuid.uuid4()

    monkeypatch.setattr("app.services.attachment_helper.upload_to_file_server", _upload)
    return stored


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _deposit_agreement(test_engine, *, attach=True, amount="400000.00", **over):
    vendor_id, _name, user_id = await seed_vendor_and_user(
        test_engine, vendor_name=f"Landlord {uuid.uuid4().hex[:6]}")
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        agreement_type="deposit", deposit_amount=Decimal(amount),
        title="Warehouse lease deposit", **over)
    if attach:
        async with _factory(test_engine)() as db:
            db.add(AgreementAttachment(
                agreement_id=agr.id, filename="lease.pdf", content_type="application/pdf",
                file_size=10, storage_key=uuid.uuid4()))
            await db.commit()
    return agr, vendor_id


def _deposit_body(agr, **over):
    body = {"title": "Lease deposit", "agreement_id": str(agr.id),
            "pa_type": "prepayment", "subtotal": "1.00"}
    body.update(over)
    return body


async def _paid_deposit(admin_client, test_engine, **agr_over):
    agr, vendor_id = await _deposit_agreement(test_engine, **agr_over)
    r = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert r.status_code == 201, r.text
    pa_id = uuid.UUID(r.json()["id"])
    async with _factory(test_engine)() as db:
        pa = await db.get(PaymentApplication, pa_id)
        pa.status = "processed"
        await db.commit()
    return agr, r.json()


def _refund(client, deposit_id, amount, reason=None, files=True):
    data = {"refund_amount": amount}
    if reason is not None:
        data["retained_reason"] = reason
    fs = [("files", ("bank-receipt.pdf", b"%PDF-1.4 refund", "application/pdf"))] if files else []
    return client.post(f"{PA_URL}/{deposit_id}/deposit-refund", data=data, files=fs or None)


# ── Agreement ────────────────────────────────────────────────────────────────

async def test_deposit_agreement_requires_amount(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine, vendor_name="Dep V1")
    base = {"title": "Lease deposit", "agreement_type": "deposit", "vendor_id": str(vendor_id),
            "valid_from": "2026-01-01", "valid_to": "2027-12-31"}
    r = await admin_client.post(AGR_URL, json=base)
    assert r.status_code == 422, r.text
    r = await admin_client.post(AGR_URL, json={**base, "deposit_amount": "400000.00"})
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["deposit_amount"]) == Decimal("400000.00")


async def test_deposit_amount_rejected_on_other_types(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine, vendor_name="Dep V2")
    r = await admin_client.post(AGR_URL, json={
        "title": "x", "agreement_type": "house_account", "vendor_id": str(vendor_id),
        "valid_from": "2026-01-01", "valid_to": "2026-12-31", "deposit_amount": "10"})
    assert r.status_code == 422, r.text


async def test_deposit_agreement_takes_no_invoice(admin_client, test_engine):
    agr, vendor_id = await _deposit_agreement(test_engine)
    inv = await _upload_invoice(admin_client, vendor_id, amount="100.00")
    cands = await admin_client.get(f"/api/v1/invoices/{inv['id']}/agreement-candidates")
    assert cands.status_code == 200, cands.text
    assert all(c["id"] != str(agr.id) for c in cands.json()["items"])
    m = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert m.status_code in (409, 422), m.text
    assert "deposit" in m.text.lower()


# ── Deposit PA ───────────────────────────────────────────────────────────────

async def test_deposit_pa_amount_locked_to_agreement(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine)
    r = await admin_client.post(PA_URL, json=_deposit_body(
        agr, subtotal="5.00", tax_amount="13.00", shipping_amount="9.00", prepayment_pct="30"))
    assert r.status_code == 201, r.text
    pa = r.json()
    assert pa["pa_type"] == "prepayment"
    assert Decimal(pa["subtotal"]) == Decimal("400000.00")
    assert Decimal(pa["tax_amount"]) == 0 and Decimal(pa["shipping_amount"]) == 0
    assert Decimal(pa["payment_amount"]) == Decimal("400000.00")
    assert pa["invoice_ids"] == [] and pa["agreement_number"] == agr.number
    assert pa["settlement_status"] == "pending"
    # PATCH cannot move the amount either
    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={"subtotal": "1.00"})
    assert r.status_code == 422, r.text
    r = await admin_client.patch(f"{PA_URL}/{pa['id']}", json={"title": "Renamed"})
    assert r.status_code == 200, r.text


async def test_deposit_pa_needs_attachment(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine, attach=False)
    r = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert r.status_code == 422, r.text
    assert "attachment" in r.text


async def test_deposit_pa_only_once(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine)
    first = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert first.status_code == 201, first.text
    second = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert second.status_code == 409, second.text
    # a cancelled deposit frees the agreement
    async with _factory(test_engine)() as db:
        pa = await db.get(PaymentApplication, uuid.UUID(first.json()["id"]))
        pa.status = "cancelled"
        await db.commit()
    third = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert third.status_code == 201, third.text


async def test_deposit_agreement_rejects_regular_pa_and_invoices(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine)
    r = await admin_client.post(PA_URL, json=_deposit_body(agr, pa_type="regular"))
    assert r.status_code == 422, r.text
    r = await admin_client.post(PA_URL, json=_deposit_body(agr, invoice_ids=[str(uuid.uuid4())]))
    assert r.status_code == 422, r.text


async def test_deposit_pa_needs_active_agreement(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine, status="draft")
    r = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert r.status_code == 422, r.text


async def test_other_agreement_types_still_refuse_prepayment(admin_client, test_engine):
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine, vendor_name="HA V")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    r = await admin_client.post(PA_URL, json=_deposit_body(agr))
    assert r.status_code == 422, r.text


# ── Refund ───────────────────────────────────────────────────────────────────

async def test_full_refund_waits_for_finance_then_settles(admin_client, test_engine, fake_upload):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    r = await _refund(admin_client, dep["id"], "400000.00")
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["pa_type"] == "settlement" and ref["status"] == "submitted"
    assert Decimal(ref["payment_amount"]) == 0
    assert Decimal(ref["deposit_refund_amount"]) == Decimal("400000.00")
    assert ref["prepayment_pa_id"] == dep["id"]
    assert fake_upload == [("bank-receipt.pdf", len(b"%PDF-1.4 refund"))]
    async with _factory(test_engine)() as db:
        task = (await db.execute(select(Task).where(
            Task.document_id == uuid.UUID(ref["id"]), Task.is_completed.is_(False)))).scalar_one()
        assert task.type == "confirm_settlement" and task.assigned_role == "finance_bp"
        deposit = await db.get(PaymentApplication, uuid.UUID(dep["id"]))
        assert deposit.settlement_status == "pending"   # not until Finance confirms

    c = await admin_client.post(f"{PA_URL}/{ref['id']}/confirm-settlement")
    assert c.status_code == 200, c.text
    assert c.json()["status"] == "processed"
    async with _factory(test_engine)() as db:
        deposit = await db.get(PaymentApplication, uuid.UUID(dep["id"]))
        assert deposit.settlement_status == "settled"
        assert deposit.settlement_variance == 0
        assert ref["pa_number"] in deposit.settlement_note
        open_tasks = (await db.execute(select(Task).where(
            Task.document_id == uuid.UUID(ref["id"]), Task.is_completed.is_(False)))).all()
        assert open_tasks == []


async def test_partial_refund_requires_reason_and_records_retained(admin_client, test_engine):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    r = await _refund(admin_client, dep["id"], "350000.00")
    assert r.status_code == 422, r.text
    assert "reason" in r.text
    r = await _refund(admin_client, dep["id"], "350000.00", reason="Floor damage repair")
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["deposit_retained_reason"] == "Floor damage repair"
    c = await admin_client.post(f"{PA_URL}/{ref['id']}/confirm-settlement")
    assert c.status_code == 200, c.text
    async with _factory(test_engine)() as db:
        deposit = await db.get(PaymentApplication, uuid.UUID(dep["id"]))
        assert deposit.settlement_status == "settled"
        assert deposit.settlement_variance == Decimal("-50000.00")
        assert "50000.00 retained" in deposit.settlement_note
        assert "Floor damage repair" in deposit.settlement_note


async def test_refund_cannot_exceed_deposit(admin_client, test_engine):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    r = await _refund(admin_client, dep["id"], "400000.01")
    assert r.status_code == 422, r.text


async def test_refund_requires_evidence(admin_client, test_engine):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    r = await _refund(admin_client, dep["id"], "400000.00", files=False)
    assert r.status_code == 422, r.text


async def test_refund_needs_paid_deposit(admin_client, test_engine):
    agr, _ = await _deposit_agreement(test_engine)
    dep = (await admin_client.post(PA_URL, json=_deposit_body(agr))).json()
    r = await _refund(admin_client, dep["id"], "400000.00")
    assert r.status_code == 409, r.text


async def test_refund_allowed_after_agreement_expired(admin_client, test_engine):
    from datetime import date, timedelta
    agr, dep = await _paid_deposit(admin_client, test_engine)
    async with _factory(test_engine)() as db:
        from app.models.agreement import PurchaseAgreement
        a = await db.get(PurchaseAgreement, agr.id)
        a.valid_from = date.today() - timedelta(days=800)
        a.valid_to = date.today() - timedelta(days=400)
        await db.commit()
    r = await _refund(admin_client, dep["id"], "400000.00")
    assert r.status_code == 201, r.text


async def test_one_live_refund_and_return_reopens(admin_client, test_engine):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    first = await _refund(admin_client, dep["id"], "400000.00")
    assert first.status_code == 201, first.text
    dup = await _refund(admin_client, dep["id"], "400000.00")
    assert dup.status_code == 409, dup.text

    ret = await admin_client.post(f"{PA_URL}/{first.json()['id']}/return-deposit-refund",
                                  json={"comment": "Not on the bank statement yet"})
    assert ret.status_code == 200, ret.text
    assert ret.json()["status"] == "rejected"
    assert "Not on the bank statement yet" in ret.json()["notes"]
    # can no longer be confirmed
    c = await admin_client.post(f"{PA_URL}/{first.json()['id']}/confirm-settlement")
    assert c.status_code == 409, c.text
    async with _factory(test_engine)() as db:
        deposit = await db.get(PaymentApplication, uuid.UUID(dep["id"]))
        assert deposit.settlement_status == "pending"
    again = await _refund(admin_client, dep["id"], "400000.00")
    assert again.status_code == 201, again.text


async def test_only_finance_confirms_or_returns(admin_client, test_engine):
    agr, dep = await _paid_deposit(admin_client, test_engine)
    ref = (await _refund(admin_client, dep["id"], "400000.00")).json()
    # Production grants both roles epms.pa.write (identity seed_phase2_keys);
    # the test matrix seeds phase-1 keys only — without this both would be
    # refused by the generic gate and the 403 below would prove nothing.
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING"))
        for role in ("requester", "finance_bp"):
            await db.execute(text(
                "INSERT INTO role_permissions(role_code,permission_key) "
                "VALUES (:r,'epms.pa.write') ON CONFLICT DO NOTHING"), {"r": role})
        await db.commit()
    requester, _ = await _client_with_user(test_engine, "requester")
    finance, _ = await _client_with_user(test_engine, "finance_bp")
    async with requester, finance:
        c = await requester.post(f"{PA_URL}/{ref['id']}/confirm-settlement")
        assert c.status_code == 403, c.text
        assert "Only Finance" in c.text
        r = await requester.post(f"{PA_URL}/{ref['id']}/return-deposit-refund",
                                 json={"comment": "x"})
        assert r.status_code == 403, r.text
        assert "Only Finance" in r.text
        # the positive half: a finance_bp user may confirm
        ok = await finance.post(f"{PA_URL}/{ref['id']}/confirm-settlement")
        assert ok.status_code == 200, ok.text


async def test_refund_endpoint_refuses_po_prepayment(admin_client, test_engine):
    """Only a deposit agreement's PA takes a refund — not an ordinary PA."""
    vendor_id, _, user_id = await seed_vendor_and_user(test_engine, vendor_name="HA V2")
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="10.00")
    await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={"agreement_id": str(agr.id)})
    from tests.test_agreement_pa import _declare_legacy_settlement
    await _declare_legacy_settlement(test_engine, inv["id"])
    pa = (await admin_client.post(PA_URL, json={
        "title": "x", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "10.00"})).json()
    r = await _refund(admin_client, pa["id"], "1.00")
    assert r.status_code == 409, r.text


# ── Evidence must exist before approval (DEV1 finding 2026-11-17) ────────────

async def _att(test_engine, agreement_id, name="lease.pdf"):
    async with _factory(test_engine)() as db:
        a = AgreementAttachment(agreement_id=agreement_id, filename=name,
                                content_type="application/pdf", file_size=10,
                                storage_key=uuid.uuid4())
        db.add(a)
        await db.commit()
        return a.id


async def test_deposit_agreement_cannot_be_submitted_without_attachment(
        admin_client, test_engine, monkeypatch):
    calls: list[str] = []

    async def _fake_delegate(doc_type, doc_id, action, comment, token, expected_step=None):
        calls.append(action)
        return {"new_status": "submitted"}

    monkeypatch.setattr("app.api.v1.agreements.delegate_action", _fake_delegate)
    agr, _ = await _deposit_agreement(test_engine, attach=False, status="draft",
                                      owner_id=None)
    async with _factory(test_engine)() as db:
        from app.models.agreement import PurchaseAgreement
        a = await db.get(PurchaseAgreement, agr.id)
        a.owner_id = a.created_by
        await db.commit()
    r = await admin_client.post(f"{AGR_URL}/{agr.id}/action", json={"action": "submit"})
    assert r.status_code == 422, r.text
    assert "Attachments" in r.text
    assert calls == []
    # the positive half: with the evidence attached it goes to the engine
    await _att(test_engine, agr.id)
    r = await admin_client.post(f"{AGR_URL}/{agr.id}/action", json={"action": "submit"})
    assert r.status_code == 200, r.text
    assert calls == ["submit"]


async def test_last_attachment_of_submitted_deposit_cannot_be_removed(
        admin_client, test_engine, monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.api.v1.agreement_attachments.delete_from_file_server", _noop)
    agr, _ = await _deposit_agreement(test_engine, attach=False)   # status active
    first = await _att(test_engine, agr.id, "lease.pdf")
    r = await admin_client.delete(f"{AGR_URL}/{agr.id}/attachments/{first}")
    assert r.status_code == 409, r.text
    second = await _att(test_engine, agr.id, "lease-v2.pdf")
    r = await admin_client.delete(f"{AGR_URL}/{agr.id}/attachments/{first}")
    assert r.status_code == 204, r.text
    r = await admin_client.delete(f"{AGR_URL}/{agr.id}/attachments/{second}")
    assert r.status_code == 409, r.text


async def test_draft_deposit_attachment_can_still_be_removed(
        admin_client, test_engine, monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.api.v1.agreement_attachments.delete_from_file_server", _noop)
    agr, _ = await _deposit_agreement(test_engine, attach=False, status="draft")
    only = await _att(test_engine, agr.id)
    r = await admin_client.delete(f"{AGR_URL}/{agr.id}/attachments/{only}")
    assert r.status_code == 204, r.text
