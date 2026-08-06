"""erp_pa_officer authorization for NC-imported PO Payment Applications.

An NC-imported PO has no PR/requester, so the standard "requester of the linked
PR" ownership rule can never grant PA creation. The erp_pa_officer additional
role (grantable to many users, layered on any base role) both grants
epms.pa.write and relaxes the requester-ownership 403 for PR-less NC POs.

These tests prove the end-to-end effect: a plain requester is blocked by the
ownership rule (the PO is not linked to a PR they raised, and they hold none of
the roles _may_create_pa_on_behalf recognises), while the SAME base-requester
carrying the erp_pa_officer role can. Note: within a session where
test_pa_on_behalf_authz.py has already run, the requester may separately hold
epms.pa.write (that file commits a requester -> epms.pa.write grant into the
session-scoped authz tables, which are never truncated) — the 403 here is still
the ownership rule, not the permission gate.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

PA_URL = "/api/v1/pa"


def _pa_payload(po_id):
    return {
        "po_id": po_id, "title": "NC PO Payment", "pa_type": "regular",
        "subtotal": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "line_items": [{"description": "X", "qty": "1", "unit": "EA", "unit_price": "100.00"}],
    }


async def _grant_erp_pa_officer_pa_write(db):
    """Register the role + its epms.pa.write grant in the shadow authz tables
    (idempotent). Only erp_pa_officer is granted pa.write here — a plain
    requester still cannot create PAs."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        "VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_defs(code,label,sort,is_active) "
        "VALUES ('erp_pa_officer','ERP PA Officer',850,true) ON CONFLICT (code) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('erp_pa_officer','epms.pa.write') ON CONFLICT DO NOTHING"))


async def _nc_three_way_po(db):
    """A committed NC-imported PO (source='nc', no PR) with a GR + matched
    invoice → satisfies the 3-way receipt gate."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    creator = await user_crud.create(db, RegisterRequest(
        email=f"ncsync-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="NC Sync", role="system_admin"))
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="NC PO", type=1,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=creator.id, pr_id=None, source="nc")
    db.add(po); await db.flush()
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=1, status="collected",
                      created_by=creator.id)
    db.add(gr); await db.flush()
    inv = Invoice(internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                  vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
                  tax_amount=Decimal("0"), total_amount=Decimal("100"),
                  invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                  status="matched", line_items=[], po_id=po.id, gr_id=gr.id,
                  uploaded_by=creator.id)
    db.add(inv); await db.flush()
    return po.id


async def _requester(db, *, erp_pa_officer: bool):
    u = await user_crud.create(db, RegisterRequest(
        email=f"req-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Req", role="requester"))
    if erp_pa_officer:
        await db.execute(text(
            "INSERT INTO user_roles(user_id, role_code) VALUES (:u,'erp_pa_officer') "
            "ON CONFLICT DO NOTHING"), {"u": str(u.id)})
    return u


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_plain_requester_cannot_create_pa_for_nc_po(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_erp_pa_officer_pa_write(db)
        po_id = await _nc_three_way_po(db)
        user = await _requester(db, erp_pa_officer=False)
        await db.commit()
    async with _client_for(user) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_erp_pa_officer_can_create_pa_for_nc_po(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_erp_pa_officer_pa_write(db)
        po_id = await _nc_three_way_po(db)
        user = await _requester(db, erp_pa_officer=True)
        await db.commit()
    async with _client_for(user) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text
    assert r.json()["pa_number"].startswith("PA-")
