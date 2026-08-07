"""Invoice ↔ Agreement matching (Phase 1A: manual route, no slips)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import PurchaseAgreement
from app.models.invoice import Invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


async def test_invoice_carries_agreement_link_columns(test_engine):
    """The new columns exist, default correctly, and round-trip."""
    vendor_id, vendor_name, user_id = await seed_vendor_and_user(test_engine)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number="PA-STMT-202607",
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=Decimal("1000.00"),
            tax_amount=Decimal("130.00"),
            total_amount=Decimal("1130.00"),
            invoice_date=date(2026, 7, 31),
            due_date=date(2026, 8, 30),
            uploaded_by=user_id,
            line_items=[],
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

        assert inv.agreement_id is None
        assert inv.agreement_number is None
        assert inv.match_route is None
        assert inv.match_route_auto is False
        assert inv.legacy_settlement is False
        assert inv.legacy_settlement_reason is None


async def _make_active_agreement(test_engine, vendor_id, created_by, **over):
    """Insert an active agreement directly — bypasses the approval chain, which
    needs a running approval-api this suite does not have."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vendor_uuid = vendor_id if isinstance(vendor_id, uuid.UUID) else uuid.UUID(str(vendor_id))
    creator_uuid = created_by if isinstance(created_by, uuid.UUID) else uuid.UUID(str(created_by))
    fields = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
        title="Test house account",
        agreement_type="house_account",
        vendor_id=vendor_uuid,
        vendor_name="Test Vendor",
        valid_from=date.today() - timedelta(days=30),
        valid_to=date.today() + timedelta(days=30),
        status="active",
        created_by=creator_uuid,
    )
    fields.update(over)
    async with factory() as db:
        agr = PurchaseAgreement(**fields)
        db.add(agr)
        await db.commit()
        await db.refresh(agr)
        return agr


async def _upload_invoice(client, vendor_id, amount="1000.00"):
    r = await client.post(INV_URL, json={
        "vendor_id": str(vendor_id),
        "vendor_invoice_number": f"STMT-{uuid.uuid4().hex[:6]}",
        "amount": amount,
        "tax_amount": "0.00",
        "currency": "CAD",
        "invoice_date": "2026-07-31",
        "due_date": "2026-08-30",
        "line_items": [{"description": "Monthly statement", "quantity": "1",
                        "unit_price": amount, "line_total": amount}],
    })
    r.raise_for_status()
    return r.json()


async def test_agreement_candidates_returns_active_same_vendor(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/agreement-candidates")
    assert r.status_code == 200, r.text
    assert str(agr.id) in [i["id"] for i in r.json()["items"]]


async def test_agreement_candidates_excludes_draft_and_cancelled(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    draft = await _make_active_agreement(test_engine, vendor_id, user_id, status="draft")
    cancelled = await _make_active_agreement(test_engine, vendor_id, user_id, status="cancelled")
    inv = await _upload_invoice(admin_client, vendor_id)

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(draft.id) not in ids
    assert str(cancelled.id) not in ids


async def test_agreement_candidates_excludes_other_vendors(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    other_vendor_id, _other_name, _other_user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Other Vendor")

    other = await _make_active_agreement(test_engine, other_vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id)

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(other.id) not in ids


async def test_expired_agreement_inside_grace_is_a_candidate(admin_client, test_engine):
    """8/31 expiry, 9/3 statement — the month's bill always lands after expiry."""
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=90),
        valid_to=date.today() - timedelta(days=5),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) in ids


async def test_expired_agreement_past_grace_is_not_a_candidate(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(
        test_engine, vendor_id, user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=200),
        valid_to=date.today() - timedelta(days=100),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, vendor_id)
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) not in ids
