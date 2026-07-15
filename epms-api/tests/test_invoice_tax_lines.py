"""Invoice tax lines — line-level tax split + header derivation (Phase 0-B2)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _make_invoice(test_engine, status: str = "unmatched") -> str:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"tax-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Tax Tester", role="ap_clerk",
        ))
        vendor = Vendor(code=f"VTAX-{uuid.uuid4().hex[:6]}", name="Tax Vendor",
                        category="Parts", contact_name="V", contact_email="v@v.com",
                        payment_terms="net30", currency="CAD")
        db.add(vendor)
        await db.flush()
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number="VI-77",
            vendor_id=vendor.id, vendor_name=vendor.name,
            amount=Decimal("100.00"), tax_amount=Decimal("0"),
            total_amount=Decimal("100.00"), currency="CAD",
            invoice_date=date(2026, 6, 1), due_date=date(2026, 7, 1),
            status=status, uploaded_by=user.id,
        )
        db.add(inv)
        await db.commit()
        return str(inv.id)


async def _role_client(test_engine, role: str) -> AsyncClient:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name=f"T {role}", role=role,
        ))
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


_TWO_LINES = [
    {"tax_code": "HST_ON", "tax_amount": "13.00", "taxable_amount": "100.00", "recoverable": True},
    {"tax_code": "PST_BC", "tax_amount": "7.00", "recoverable": False},
]


@pytest.mark.asyncio
async def test_put_tax_lines_derives_header(admin_client, test_engine):
    inv_id = await _make_invoice(test_engine)
    r = await admin_client.put(f"/api/v1/invoices/{inv_id}/tax-lines", json=_TWO_LINES)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tax_amount"] == "20.00"
    assert body["total_amount"] == "120.00"
    assert [ln["tax_code"] for ln in body["lines"]] == ["HST_ON", "PST_BC"]
    assert body["lines"][1]["recoverable"] is False

    r = await admin_client.get(f"/api/v1/invoices/{inv_id}/tax-lines")
    assert r.status_code == 200
    assert len(r.json()["lines"]) == 2


@pytest.mark.asyncio
async def test_put_replaces_full_set(admin_client, test_engine):
    inv_id = await _make_invoice(test_engine)
    await admin_client.put(f"/api/v1/invoices/{inv_id}/tax-lines", json=_TWO_LINES)
    r = await admin_client.put(f"/api/v1/invoices/{inv_id}/tax-lines",
                               json=[{"tax_code": "GST", "tax_amount": "5.00"}])
    assert r.status_code == 200
    body = r.json()
    assert body["tax_amount"] == "5.00"
    assert body["total_amount"] == "105.00"
    assert len(body["lines"]) == 1


@pytest.mark.asyncio
async def test_negative_tax_rejected(admin_client, test_engine):
    inv_id = await _make_invoice(test_engine)
    r = await admin_client.put(f"/api/v1/invoices/{inv_id}/tax-lines",
                               json=[{"tax_code": "GST", "tax_amount": "-1.00"}])
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_paid_invoice_locked(admin_client, test_engine):
    inv_id = await _make_invoice(test_engine, status="paid")
    r = await admin_client.put(f"/api/v1/invoices/{inv_id}/tax-lines",
                               json=[{"tax_code": "GST", "tax_amount": "5.00"}])
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_requires_invoice_upload_permission(test_engine, mocker):
    """Gate is the Access Control Matrix (invoice_upload), not hardcoded roles.
    With no matrix grant, a requester is denied; system_admin always passes.

    The matrix is mocked: it now comes from the identity authz hub over HTTP, so
    an unmocked run asserts against whatever the live matrix happens to grant
    (and silently passes wherever identity is unreachable) instead of the gate.
    """
    mocker.patch("app.core.authz_client.get_matrix",
                 return_value={"requester": {"invoice_upload": False}})
    inv_id = await _make_invoice(test_engine)
    async with await _role_client(test_engine, "requester") as c:
        r = await c.put(f"/api/v1/invoices/{inv_id}/tax-lines",
                        json=[{"tax_code": "GST", "tax_amount": "5.00"}])
    assert r.status_code == 403
