"""Regression: an agreement's invoice list follows the AGREEMENT's read gate.

Bug: `GET /invoices?agreement_id=` inherited the invoice module's PO-chain
scope. An agreement invoice has po_id NULL by construction, so that scope can
never match one, and the two conditions OR-ed beside it only catch invoices the
caller personally uploaded or holds an open task on. A requester — who holds
epms.agreement.read and view_invoice in the default matrix — therefore opened
an agreement detail page and read "No invoices matched to this agreement yet"
with seven matched to it, while the receipts reconciling those very invoices
were listed in full higher up the same page. The page did not show less than it
should; it asserted something false.

Every other thing that page renders is gated on epms.agreement.read alone with
no row scope (the agreement itself, its receipts). The invoice sub-list was the
one member inheriting a scope its siblings do not have.

The widening is confined to the agreement_id filter — the second test is the
one that matters for that: the same requester's UNFILTERED invoice list must
still be scoped exactly as before.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.approval import ApprovalEvent
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor


async def _seed(sf, *, owner: bool = True):
    """A requester who did NOT create the agreement and did NOT upload the
    invoice. With owner=True they are the agreement's named Owner — the only
    thing connecting them to it; with owner=False, nothing connects them at
    all and every read below must stay shut."""
    tok = uuid.uuid4().hex[:8]
    async with sf() as db:
        requester = User(id=uuid.uuid4(), email=f"req-{tok}@example.com",
                         hashed_password=hash_password("x"), full_name="Req",
                         role="requester", is_active=True)
        ap = User(id=uuid.uuid4(), email=f"ap-{tok}@example.com",
                  hashed_password=hash_password("x"), full_name="AP",
                  role="ap_clerk", is_active=True)
        vendor = Vendor(id=uuid.uuid4(), code=f"V{tok}", name="Princess Auto",
                        category="general", contact_name="N/A",
                        contact_email="v@example.com", payment_terms="net30")
        db.add_all([requester, ap, vendor])
        await db.flush()
        # epms.agreement.read is an identity-migration key, not part of
        # conftest's default matrix — seed it the way the agreement suites do.
        await db.execute(text(
            "INSERT INTO permission_defs (key, module, label, sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions (role_code, permission_key) "
            "VALUES ('requester','epms.agreement.read') ON CONFLICT DO NOTHING"))
        # epms.pa.write too — production's default matrix grants it to
        # `requester`, and without it the PA assertion below would be answered
        # by the permission gate (403) and would prove nothing about the row
        # scope it is actually there to test.
        await db.execute(text(
            "INSERT INTO permission_defs (key, module, label, sort) "
            "VALUES ('epms.pa.write','epms','Create Payment Applications',105) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions (role_code, permission_key) "
            "VALUES ('requester','epms.pa.write') ON CONFLICT DO NOTHING"))
        agr = PurchaseAgreement(
            id=uuid.uuid4(), number=f"AGR-T{tok}", title="House account",
            agreement_type="house_account", vendor_id=vendor.id, vendor_name=vendor.name,
            valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
            created_by=ap.id, owner_id=requester.id if owner else None)
        db.add(agr)
        await db.flush()
        # Matched to the agreement, uploaded by AP: po_id NULL, as every
        # agreement invoice is.
        on_agr = Invoice(id=uuid.uuid4(), internal_ref=f"INV-A{tok}",
                         vendor_invoice_number=f"VI-A{tok}",
                         vendor_id=vendor.id, vendor_name=vendor.name,
                         amount=Decimal("100"), total_amount=Decimal("113"),
                         invoice_date=date(2026, 7, 1), due_date=date(2026, 8, 1),
                         uploaded_by=ap.id, status="matched",
                         po_id=None, agreement_id=agr.id,
                         agreement_number=agr.number, agreement_type="house_account")
        # Unrelated to any agreement, also someone else's — the control for
        # "the general list stays scoped".
        loose = Invoice(id=uuid.uuid4(), internal_ref=f"INV-B{tok}",
                        vendor_invoice_number=f"VI-B{tok}",
                        vendor_id=vendor.id, vendor_name=vendor.name,
                        amount=Decimal("50"), total_amount=Decimal("56.50"),
                        invoice_date=date(2026, 7, 1), due_date=date(2026, 8, 1),
                        uploaded_by=ap.id, status="unmatched", po_id=None)
        # One approval event, so the timeline test below proves the actor JOIN
        # resolves a name and not just that the route answers 200.
        db.add(ApprovalEvent(
            id=uuid.uuid4(), document_type="agr", document_id=agr.id,
            document_number=agr.number, step_idx=0, action="approve",
            actor_id=ap.id, actor_role="ap_clerk"))
        db.add_all([on_agr, loose])
        await db.commit()
        return requester.id, agr.id, on_agr.id, loose.id


def _client(user_id):
    token = create_access_token(str(user_id), "requester")
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_agreement_filter_shows_every_invoice_on_that_agreement(test_engine):
    """The owner sees the whole agreement, including invoices they never
    touched — that is the point of being the owner."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, agr_id, on_agr_id, _ = await _seed(sf)

    async with _client(uid) as c:
        r = await c.get("/api/v1/invoices", params={"agreement_id": str(agr_id), "page_size": 200})
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in r.json()["items"]}
    # Before the fix: empty — po_id is NULL so the PO-chain scope excludes it,
    # and this requester neither uploaded it nor holds a task on it.
    assert str(on_agr_id) in ids


@pytest.mark.asyncio
async def test_unfiltered_list_keeps_its_scope(test_engine):
    """The widening must not leak past the agreement_id filter."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, _, on_agr_id, loose_id = await _seed(sf)

    async with _client(uid) as c:
        r = await c.get("/api/v1/invoices", params={"page_size": 200})
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in r.json()["items"]}
    assert str(loose_id) not in ids, "someone else's invoice must stay out of the general list"
    assert str(on_agr_id) not in ids, "agreement access widens the agreement view, not the whole list"


@pytest.mark.asyncio
async def test_without_agreement_read_the_agreement_view_stays_scoped(test_engine):
    """The gate is a real gate: revoke epms.agreement.read and the widening
    goes with it — otherwise this would be 'any requester sees any invoice
    that happens to carry an agreement_id'."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, agr_id, on_agr_id, _ = await _seed(sf)
    async with sf() as db:
        await db.execute(text(
            "DELETE FROM role_permissions "
            "WHERE role_code='requester' AND permission_key='epms.agreement.read'"))
        await db.commit()

    try:
        async with _client(uid) as c:
            r = await c.get("/api/v1/invoices", params={"agreement_id": str(agr_id), "page_size": 200})
        assert r.status_code == 200, r.text
        assert str(on_agr_id) not in {i["id"] for i in r.json()["items"]}
    finally:
        async with sf() as db:
            await db.execute(text(
                "INSERT INTO role_permissions (role_code, permission_key) "
                "VALUES ('requester','epms.agreement.read') ON CONFLICT DO NOTHING"))
            await db.commit()


@pytest.mark.asyncio
async def test_detail_of_an_agreement_invoice_opens_too(test_engine):
    """The listing and the detail must agree. Listing the invoice on the
    agreement page and then 404-ing on the click is the same lie in a different
    place."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, _, on_agr_id, loose_id = await _seed(sf)

    async with _client(uid) as c:
        ok = await c.get(f"/api/v1/invoices/{on_agr_id}")
        denied = await c.get(f"/api/v1/invoices/{loose_id}")
    assert ok.status_code == 200, ok.text
    # …and the widening still stops at invoices that belong to an agreement.
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_an_unrelated_requester_sees_none_of_it(test_engine):
    """The other half of the rule. epms.agreement.read says this person works
    with agreements; it does not say WHICH. Someone with no connection to a
    house account — not its creator, not its owner, not in its department —
    must not be able to list it, open it, read its invoices, or raise a payment
    against it.

    Every one of these passed before the row scope existed: `requester` holds
    epms.agreement.read AND epms.agreement.write AND epms.pa.write in the
    default matrix, and no agreement endpoint consulted a row scope at all.
    """
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, agr_id, on_agr_id, _ = await _seed(sf, owner=False)

    async with _client(uid) as c:
        listing = await c.get("/api/v1/agreements", params={"page_size": 200})
        detail = await c.get(f"/api/v1/agreements/{agr_id}")
        invoices = await c.get("/api/v1/invoices", params={"agreement_id": str(agr_id), "page_size": 200})
        invoice = await c.get(f"/api/v1/invoices/{on_agr_id}")
        receipts = await c.get("/api/v1/agreement-receipts", params={"agreement_id": str(agr_id)})
        pa = await c.post("/api/v1/pa", json={
            "title": "Payment", "agreement_id": str(agr_id),
            "invoice_ids": [str(on_agr_id)], "pa_type": "regular",
            "subtotal": "100", "tax_amount": "13",
        })

    assert listing.status_code == 200, listing.text
    assert str(agr_id) not in {a["id"] for a in listing.json()["items"]}
    assert detail.status_code == 404
    assert invoices.status_code == 200 and invoices.json()["items"] == []
    assert invoice.status_code == 404
    assert receipts.status_code == 200 and receipts.json()["items"] == []
    # 404 (not 422): the payment is refused because this agreement is not this
    # caller's to spend, which is indistinguishable from it not existing.
    assert pa.status_code == 404, pa.text


@pytest.mark.asyncio
async def test_the_owner_can_still_raise_the_payment(test_engine):
    """…and the gate must not swing so far that the person actually
    responsible for the agreement is locked out of paying it."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    uid, agr_id, _, _ = await _seed(sf, owner=True)

    async with _client(uid) as c:
        listing = await c.get("/api/v1/agreements", params={"page_size": 200})
        detail = await c.get(f"/api/v1/agreements/{agr_id}")
        events = await c.get(f"/api/v1/agreements/{agr_id}/events")

    assert str(agr_id) in {a["id"] for a in listing.json()["items"]}
    assert detail.status_code == 200
    # The timeline's data source. The name is the whole point: the page
    # rendered role labels and green ticks with nobody against them because
    # this endpoint did not exist.
    assert events.status_code == 200, events.text
    assert [(e["action"], e["actor_name"]) for e in events.json()] == [("approve", "AP")]
