"""POST /invoices/{id}/billing-period — the agreement's owner may move a
recurring invoice to another period, not only AP (user decision 2026-10-13).

Driven over HTTP because the change is in the endpoint's gate; the move itself
is covered at the crud layer in test_agreement_schedule_claim.py.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


def _sf(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _user(db, role):
    return await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=f"T {role}", role=role))


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _seed(test_engine, *, with_pa=False):
    """A recurring agreement owned by a requester, and an invoice on its first
    period uploaded by somebody else — so the owner is neither the uploader
    nor AP nor a match-task holder: ownership is the only way in."""
    async with _sf(test_engine)() as db:
        uploader = await _user(db, "procurement_officer")
        owner = await _user(db, "requester")
        stranger = await _user(db, "requester")
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Lawncare", category="supplier",
                        contact_name="AP", contact_email="ap@lawn.example")
        db.add(vendor)
        await db.flush()
        agr = PurchaseAgreement(
            number=f"AGR-202610-T{uuid.uuid4().hex[:11]}", title="Lawncare",
            agreement_type="recurring", vendor_id=vendor.id, vendor_name=vendor.name,
            valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
            recurring_type="monthly", expected_invoice_day=5,
            expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
            overdue_after_days=7, status="active", created_by=uploader.id, owner_id=owner.id)
        db.add(agr)
        await db.flush()
        await sched_crud.ensure_period_rows(db, agr)
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number=f"B{uuid.uuid4().hex[:6]}",
            vendor_id=vendor.id, vendor_name=vendor.name,
            amount=Decimal("1200.00"), tax_amount=Decimal("0"), total_amount=Decimal("1200.00"),
            currency="CAD", invoice_date=date(2026, 2, 3), due_date=date(2026, 3, 3),
            status="matched", line_items=[], uploaded_by=uploader.id,
            matched_by=uploader.id, match_route="agreement")
        db.add(inv)
        await db.flush()
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None
        inv.agreement_id = agr.id
        inv.agreement_number = agr.number
        inv.agreement_type = agr.agreement_type
        inv.schedule_id = row.id
        if with_pa:
            db.add(PaymentApplication(
                id=uuid.uuid4(), pa_number=f"PA-T-{uuid.uuid4().hex[:6]}", title="t",
                vendor_id=vendor.id, vendor_name=vendor.name, invoice_ids=[str(inv.id)],
                gr_ids=[], subtotal=Decimal("1200"), payment_amount=Decimal("1200"),
                created_by=uploader.id, agreement_id=agr.id))
        target = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id,
                   AgreementPaymentSchedule.id != row.id)
            .order_by(AgreementPaymentSchedule.sequence))).scalars().first()
        await db.commit()
        return dict(owner=owner, stranger=stranger, inv_id=inv.id,
                    current_id=row.id, target_id=target.id)


async def test_agreement_owner_can_move_the_billing_period(test_engine):
    s = await _seed(test_engine)
    async with _client_for(s["owner"]) as c:
        r = await c.post(f"{INV_URL}/{s['inv_id']}/billing-period",
                         json={"schedule_id": str(s["target_id"])})
    assert r.status_code == 200, r.text
    assert r.json()["schedule_id"] == str(s["target_id"])


async def test_a_requester_who_does_not_own_the_agreement_is_refused(test_engine):
    """The admission test above is only meaningful next to this one: a
    requester who is not the owner (nor uploader / AP / task holder) still
    gets 403, so the owner got in by ownership and not by being a requester."""
    s = await _seed(test_engine)
    async with _client_for(s["stranger"]) as c:
        r = await c.post(f"{INV_URL}/{s['inv_id']}/billing-period",
                         json={"schedule_id": str(s["target_id"])})
    assert r.status_code == 403, r.text
    async with _sf(test_engine)() as db:
        inv = (await db.execute(select(Invoice).where(Invoice.id == s["inv_id"]))).scalar_one()
        assert inv.schedule_id == s["current_id"]


async def test_the_owner_cannot_move_an_invoice_a_pa_already_covers(test_engine):
    s = await _seed(test_engine, with_pa=True)
    async with _client_for(s["owner"]) as c:
        r = await c.post(f"{INV_URL}/{s['inv_id']}/billing-period",
                         json={"schedule_id": str(s["target_id"])})
    assert r.status_code == 422, r.text
    assert "payment application" in r.json()["detail"].lower()


async def test_the_owner_can_read_the_invoice_and_its_chain(test_engine):
    """The page decides whether to show the button off the chain (a PA already
    covering the invoice hides it). The chain used to apply a shorter read gate
    than the invoice itself, so an owning requester could open the invoice and
    then 404 on its chain."""
    s = await _seed(test_engine)
    # requester holds epms.agreement.read in production; conftest's default
    # matrix only reproduces the phase-1 keys (same per-test seeding as
    # test_agreement_receipt_api.py).
    async with _sf(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('requester','epms.agreement.read') ON CONFLICT DO NOTHING"))
        await db.commit()
    try:
        async with _client_for(s["owner"]) as c:
            r_inv = await c.get(f"{INV_URL}/{s['inv_id']}")
            r_chain = await c.get(f"{INV_URL}/{s['inv_id']}/chain")
        assert r_inv.status_code == 200, r_inv.text
        assert r_chain.status_code == 200, r_chain.text
        async with _client_for(s["stranger"]) as c:
            assert (await c.get(f"{INV_URL}/{s['inv_id']}/chain")).status_code == 404
    finally:
        async with _sf(test_engine)() as db:
            await db.execute(text(
                "DELETE FROM role_permissions "
                "WHERE role_code='requester' AND permission_key='epms.agreement.read'"))
            await db.commit()
