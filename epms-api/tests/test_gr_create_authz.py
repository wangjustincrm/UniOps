"""POST /gr authorization: matrix-driven, honoring ADDITIONAL roles.

The Create GR gate must admit anyone whose effective role union (JWT primary
role ∪ identity's user_roles) is granted `epms.gr.receive` in the Access
Control matrix — the same permission that gates every other warehouse GR
action (acknowledge / collect / confirm). A user whose PRIMARY role is
`requester` but who carries `warehouse_staff` as an ADDITIONAL role must be
able to create GRs; a plain requester must not (physical POs), yet keeps the
PR-requester bypass on service POs (confirm-delivery flow).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

GR_URL = "/api/v1/gr"


def _gr_payload(po_id):
    return {
        "po_id": str(po_id),
        "title": "Delivery",
        "currency": "CAD",
        "line_items": [{
            "description": "Hydraulic Pump", "qty_ordered": "2",
            "qty_received": "2", "unit": "EA", "unit_price": "350.00",
            "condition": "good",
        }],
    }


async def _grant_gr_receive(db):
    """Register the phase-2 `epms.gr.receive` key + its warehouse_staff grant
    in the shadow authz tables (idempotent — conftest's default matrix seeds
    only the 17 phase-1 keys)."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        "VALUES ('epms.gr.receive','epms','Receive Goods',103) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('warehouse_staff','epms.gr.receive') ON CONFLICT DO NOTHING"))


async def _issued_po(db, *, po_type: int = 1, pr_requester=None, pr_owner=None):
    """A committed issued PO (physical by default). When pr_requester is given,
    an approved PR raised by that user is linked (service confirm-delivery);
    pr_owner additionally names a different service owner on that PR."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    creator = await user_crud.create(db, RegisterRequest(
        email=f"proc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Proc", role="procurement_officer"))
    pr_id = None
    if pr_requester is not None:
        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="Svc PR",
                             type=po_type, status="approved", vendor_id=v.id,
                             vendor_name="Acme", created_by=pr_requester.id,
                             owner_id=pr_owner.id if pr_owner is not None else None)
        db.add(pr); await db.flush()
        pr_id = pr.id
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="PO", type=po_type,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=creator.id, pr_id=pr_id)
    db.add(po); await db.flush()
    return po.id


async def _user(db, *, base_role: str, additional: str | None = None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="U", role=base_role))
    if additional:
        await db.execute(text(
            "INSERT INTO user_roles(user_id, role_code) VALUES (:u,:r) "
            "ON CONFLICT DO NOTHING"), {"u": str(u.id), "r": additional})
    return u


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _setup(test_engine, *, base_role, additional=None, po_type=1, link_pr_to_user=False):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_gr_receive(db)
        user = await _user(db, base_role=base_role, additional=additional)
        po_id = await _issued_po(db, po_type=po_type,
                                 pr_requester=user if link_pr_to_user else None)
        await db.commit()
    return user, po_id


@pytest.mark.asyncio
async def test_warehouse_staff_base_role_can_create_gr(test_engine):
    user, po_id = await _setup(test_engine, base_role="warehouse_staff")
    async with _client_for(user) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_requester_with_warehouse_staff_additional_role_can_create_gr(test_engine):
    """The bug: JWT primary role is `requester`, warehouse_staff granted as an
    ADDITIONAL role via Portal Admin — Create GR must succeed."""
    user, po_id = await _setup(test_engine, base_role="requester",
                               additional="warehouse_staff")
    async with _client_for(user) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_plain_requester_cannot_create_physical_gr(test_engine):
    user, po_id = await _setup(test_engine, base_role="requester")
    async with _client_for(user) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_pr_requester_can_create_service_gr_without_warehouse_permission(test_engine):
    """Service PO confirm-delivery bypass: the requester of the linked PR may
    create the GR even with no warehouse permission at all."""
    user, po_id = await _setup(test_engine, base_role="requester",
                               po_type=4, link_pr_to_user=True)
    async with _client_for(user) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 201, r.text


# ── Service owner admission ───────────────────────────────────────────────────
#
# A service/project PR can name an OWNER other than its requester, and the
# completion-date sweep routes the confirm_receipt task there. That task's deep
# link goes straight to /gr/new?poId=, so this gate has to admit the owner or
# the reminder walks its recipient into a 403.


async def _service_po_with_owner(test_engine):
    """A type-4 PO whose PR was raised by one person and owned by another.
    Returns (requester, owner, outsider, po_id) — all plain requesters with no
    warehouse permission whatsoever."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_gr_receive(db)
        requester = await _user(db, base_role="requester")
        owner = await _user(db, base_role="requester")
        outsider = await _user(db, base_role="requester")
        po_id = await _issued_po(db, po_type=4, pr_requester=requester, pr_owner=owner)
        await db.commit()
    return requester, owner, outsider, po_id


@pytest.mark.asyncio
async def test_pr_owner_can_create_service_gr_without_warehouse_permission(test_engine):
    _requester, owner, _outsider, po_id = await _service_po_with_owner(test_engine)
    async with _client_for(owner) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_naming_an_owner_does_not_strip_the_requester(test_engine):
    """Union, not replacement: an admin who raised a service PR for an absent
    engineer still has to be able to receive it."""
    requester, _owner, _outsider, po_id = await _service_po_with_owner(test_engine)
    async with _client_for(requester) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_a_requester_who_is_neither_is_still_refused(test_engine):
    """The denial half — without it, the two admissions above would also pass
    if the service branch had simply stopped checking identity."""
    _requester, _owner, outsider, po_id = await _service_po_with_owner(test_engine)
    async with _client_for(outsider) as c:
        r = await c.post(GR_URL, json=_gr_payload(po_id))
    assert r.status_code == 403, r.text
