"""Regression: a requester-BASE user who ALSO holds a genuinely unrestricted
role (e.g. procurement_manager) must see ALL invoices — not just the ones they
personally uploaded.

Bug: list_invoices set `own_uploads = user_id` whenever the JWT base role was
"requester", independent of whether the resolved scope was actually restricted.
For requester + unrestricted-role, build_scope returns po_subq=None /
restrict=False (unrestricted → should see everything), but own_uploads then
became the SOLE condition in invoice_crud.get_all's OR, collapsing visibility
to "only invoices I uploaded". Mirror image of the dept_admin drift
(test_dept_admin_scope.py). Fix: gate own_uploads on scope["restrict"] too,
matching the adjacent task_uid gating.
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
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor


@pytest.mark.asyncio
async def test_requester_with_unrestricted_role_sees_others_invoices(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tok = uuid.uuid4().hex[:8]
    async with sf() as db:
        # requester (JWT base role) who ALSO holds procurement_manager (unrestricted)
        u = User(id=uuid.uuid4(), email=f"req-{tok}@example.com",
                 hashed_password=hash_password("x"), full_name="Req PM",
                 role="requester", is_active=True)
        other = User(id=uuid.uuid4(), email=f"ap-{tok}@example.com",
                     hashed_password=hash_password("x"), full_name="AP",
                     role="ap_clerk", is_active=True)
        vendor = Vendor(id=uuid.uuid4(), code=f"V{tok}", name="V",
                        category="general", contact_name="N/A",
                        contact_email="v@example.com", payment_terms="net30")
        db.add_all([u, other, vendor])
        await db.flush()
        # give u the extra unrestricted role, and grant view_invoice so the
        # Access-Control-Matrix gate in list_invoices passes.
        await db.execute(text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'procurement_manager')"),
                         {"u": str(u.id)})
        await db.execute(text("INSERT INTO role_permissions (role_code, permission_key) VALUES ('procurement_manager', 'view_invoice') ON CONFLICT DO NOTHING"))
        # an invoice uploaded by SOMEONE ELSE (AP), not tied to any of u's POs
        inv = Invoice(id=uuid.uuid4(), internal_ref=f"INV-{tok}",
                      vendor_invoice_number=f"VI-{tok}",
                      vendor_id=vendor.id, vendor_name="V",
                      amount=Decimal("10"), total_amount=Decimal("10"),
                      invoice_date=date(2024, 1, 1), due_date=date(2024, 1, 1),
                      uploaded_by=other.id, status="unmatched", po_id=None)
        db.add(inv)
        await db.commit()
        inv_id, uid = inv.id, u.id

    token = create_access_token(str(uid), "requester")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        r = await c.get("/api/v1/invoices", params={"page_size": 200})
        assert r.status_code == 200, r.text
        ids = {i["id"] for i in r.json()["items"]}

    # Before the fix: own_uploads collapses the OR to uploaded_by==u → absent.
    assert str(inv_id) in ids, "unrestricted requester+PM must see invoices uploaded by others"
