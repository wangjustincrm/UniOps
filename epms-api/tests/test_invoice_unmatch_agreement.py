"""POST /invoices/{id}/unmatch-agreement.

An agreement match writes more than the invoice header: it claims a billing
period (recurring) or a milestone stage, may claim house-account receipts,
raises a confirm_period / attach_receipts task, and counts toward the
agreement's consumed_amount. Unmatching has to hand every one of those back,
or the period stays "received" against an invoice that no longer backs it
and no later invoice can ever claim it.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.admin_audit_log import AdminAuditLog
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.task import Task
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


def _sf(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _recurring_matched(admin_client, test_engine):
    """A recurring agreement with period rows, and an invoice AP matched to it
    that claimed one of those periods."""
    from app.crud import agreement_schedule as sched_crud

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5, owner_id=user_id,
        expected_amount_per_period=Decimal("1000.00"), tolerance_pct=Decimal("5.00"),
        valid_from=date.today() - timedelta(days=90))
    async with _sf(test_engine)() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        await sched_crud.ensure_period_rows(db, fresh)
        await db.commit()

    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00",
                                invoice_date=date.today().isoformat())
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    matched = r.json()
    assert matched["status"] == "matched", matched
    assert matched["schedule_id"], "precondition: the match claimed a billing period"
    return agr, matched


async def _add_pa(test_engine, agr, invoice_id, status="draft"):
    async with _sf(test_engine)() as db:
        pa = PaymentApplication(
            id=uuid.uuid4(), pa_number=f"PA-UMA-{uuid.uuid4().hex[:8]}", title="Unmatch AGR PA",
            vendor_id=agr.vendor_id, vendor_name=agr.vendor_name,
            invoice_ids=[invoice_id], gr_ids=[],
            subtotal=Decimal("1000.00"), payment_amount=Decimal("1000.00"),
            tax_amount=Decimal("0.00"), currency="CAD", status=status,
            created_by=agr.created_by,
        )
        db.add(pa)
        await db.commit()
        return pa.pa_number


async def test_unmatch_agreement_returns_invoice_and_releases_the_period(admin_client, test_engine):
    agr, inv = await _recurring_matched(admin_client, test_engine)
    schedule_id = uuid.UUID(inv["schedule_id"])

    async with _sf(test_engine)() as db:
        consumed = (await db.execute(select(PurchaseAgreement.consumed_amount).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        confirm_open = (await db.execute(select(Task).where(
            Task.type == "confirm_period", Task.document_id == agr.id,
            Task.is_completed.is_(False)))).scalars().all()
    assert consumed == Decimal("1000.00")
    assert confirm_open, "precondition: the claimed period raised a confirm_period task"

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "Wrong agreement — belongs to the PO"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "unmatched"
    assert body["agreement_id"] is None
    assert body["agreement_number"] is None
    assert body["match_route"] is None
    assert body["schedule_id"] is None
    assert body["matched_at"] is None

    async with _sf(test_engine)() as db:
        row = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.id == schedule_id))).scalar_one()
        consumed = (await db.execute(select(PurchaseAgreement.consumed_amount).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
        confirm_open = (await db.execute(select(Task).where(
            Task.type == "confirm_period", Task.document_id == agr.id,
            Task.is_completed.is_(False)))).scalars().all()
        audit = (await db.execute(select(AdminAuditLog).where(
            AdminAuditLog.record_id == uuid.UUID(inv["id"]),
            AdminAuditLog.action == "unmatch"))).scalars().one()
    assert row.status == "pending" and row.invoice_id is None, \
        "the period must be claimable again by the right invoice"
    assert consumed == Decimal("0"), "the agreement must shed the invoice from consumed_amount"
    assert confirm_open == [], "nobody should be asked to confirm a period no invoice backs"
    assert audit.after["scope"] == "agreement"
    assert audit.after["reason"] == "Wrong agreement — belongs to the PO"
    assert audit.before["agreement_number"] == agr.number
    assert audit.before["schedule_id"] == str(schedule_id)


async def test_unmatched_invoice_can_be_matched_again(admin_client, test_engine):
    agr, inv = await _recurring_matched(admin_client, test_engine)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "re-do"})
    assert r.status_code == 200, r.text
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"


async def test_unmatch_agreement_refused_while_a_live_pa_lists_the_invoice(admin_client, test_engine):
    agr, inv = await _recurring_matched(admin_client, test_engine)
    pa_number = await _add_pa(test_engine, agr, inv["id"])

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "x"})
    assert r.status_code == 422, r.text
    assert pa_number in r.json()["detail"]
    async with _sf(test_engine)() as db:
        still = (await db.execute(select(Invoice).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
    assert still.agreement_id == agr.id and still.status == "matched"


async def test_unmatch_agreement_allowed_once_the_pa_is_cancelled(admin_client, test_engine):
    """Affirmative twin of the refusal above: the gate is the LIVE PA, not 'any PA'."""
    agr, inv = await _recurring_matched(admin_client, test_engine)
    await _add_pa(test_engine, agr, inv["id"], status="cancelled")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "PA was cancelled"})
    assert r.status_code == 200, r.text


async def test_unmatch_agreement_refused_on_a_po_invoice(admin_client, test_engine):
    vendor_id, _name, _uid = await seed_vendor_and_user(test_engine)
    inv = await _upload_invoice(admin_client, vendor_id)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "x"})
    assert r.status_code == 422, r.text


async def test_unmatch_agreement_needs_the_match_permission(
    admin_client, requester_client, test_engine
):
    _agr, inv = await _recurring_matched(admin_client, test_engine)
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                    json={"reason": "not mine"})
    assert r.status_code == 403, r.text


async def test_unmatch_agreement_clears_house_account_settlement(admin_client, test_engine):
    """A 'settled without receipt' declaration was made about this invoice on
    this agreement — it must not survive the unmatch and follow the invoice
    to whatever it is matched to next."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="250.00")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert r.status_code == 200, r.text
    async with _sf(test_engine)() as db:
        row = (await db.execute(select(Invoice).where(
            Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        row.legacy_settlement = True
        row.legacy_settlement_reason = "backlog"
        await db.commit()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-agreement",
                                json={"reason": "wrong vendor account"})
    assert r.status_code == 200, r.text
    assert r.json()["legacy_settlement"] is False
    assert r.json()["legacy_settlement_reason"] is None
    async with _sf(test_engine)() as db:
        open_attach = (await db.execute(select(Task).where(
            Task.type == "attach_receipts", Task.document_id == uuid.UUID(inv["id"]),
            Task.is_completed.is_(False)))).scalars().all()
    assert open_attach == []


async def test_unmatch_po_message_points_at_unmatch_agreement(admin_client, test_engine):
    _agr, inv = await _recurring_matched(admin_client, test_engine)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po", json={"reason": "x"})
    assert r.status_code == 422, r.text
    assert "Unmatch Agreement" in r.json()["detail"]
