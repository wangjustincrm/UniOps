"""ISS-0155 — an unrestricted ADDITIONAL role lifts a Requester's "Own" scope
only on the documents that role may itself view.

Payment Officer is outside the restricted role set, so build_scope used to
return pr_subq = po_subq = None ("see everything") for a Requester who also
holds it, and the Requester's own View PR / View GR then opened every PR and
GR in the company. PRD §1.3: "All PRs, provided the role has View PR" — and
Payment Officer holds neither View PR nor View GR (only View PO / Invoice / PA,
which it needs to pay). Expected: own PRs / GRs only, POs still company-wide.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.department import Department
from app.models.gr import GoodsReceipt
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio


async def _world(test_engine, extra_role: str):
    """A Requester holding `extra_role`, with one PR→PO→GR chain of their own
    and one raised by someone else in another department."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    ids: dict[str, uuid.UUID] = {}
    async with sf() as db:
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY, gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL, supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL, updated_at timestamptz NOT NULL DEFAULT now())"))
        fin = Department(code=f"F{tag[:5]}", name=f"Finance {tag}", is_active=True)
        ops = Department(code=f"O{tag[:5]}", name=f"Ops {tag}", is_active=True)
        db.add_all([fin, ops])
        await db.flush()
        me = User(id=uuid.uuid4(), email=f"me-{tag}@example.com", hashed_password=hash_password("x"),
                  full_name="Req + extra", role="requester", is_active=True, department_id=fin.id)
        other = User(id=uuid.uuid4(), email=f"o-{tag}@example.com", hashed_password=hash_password("x"),
                     full_name="Someone else", role="requester", is_active=True, department_id=ops.id)
        vendor = Vendor(id=uuid.uuid4(), code=f"V-{tag}", name="V", category="supplier",
                        contact_name="A", contact_email="a@example.com")
        db.add_all([me, other, vendor])
        await db.flush()
        await db.execute(sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
                         {"u": str(me.id), "r": extra_role})
        await db.commit()
        ids.update(me=me.id, other=other.id, vendor=vendor.id, fin=fin.id)

    for who in ("me", "other"):
        pr_id, po_id, gr_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        n = f"{who}-{tag}"
        async with sf() as db:
            db.add(PurchaseRequest(id=pr_id, number=f"PR-{n}", title="t", type=1, status="approved",
                                   currency="CAD", amount=Decimal("10"), created_by=ids[who]))
            await db.commit()
        async with sf() as db:
            db.add(PurchaseOrder(id=po_id, number=f"PO-{n}", title="t", type=1, status="approved",
                                 currency="CAD", subtotal=Decimal("10"), tax_rate=Decimal("0"),
                                 tax_amount=Decimal("0"), total=Decimal("10"),
                                 vendor_id=ids["vendor"], vendor_name="V", pr_id=pr_id,
                                 created_by=ids[who]))
            await db.commit()
        async with sf() as db:
            db.add(GoodsReceipt(id=gr_id, number=f"GR-{n}", title="t", status="confirmed",
                                po_id=po_id, po_number=f"PO-{n}", pr_id=pr_id, pr_number=f"PR-{n}",
                                vendor_id=ids["vendor"], vendor_name="V", gr_type="physical",
                                procurement_type=1, currency="CAD", created_by=ids[who]))
            await db.commit()
        ids.update({f"{who}_pr": pr_id, f"{who}_po": po_id, f"{who}_gr": gr_id})
    return ids


def _client(user_id: uuid.UUID) -> AsyncClient:
    token = create_access_token(str(user_id), "requester")
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _ids(c: AsyncClient, url: str) -> set[str]:
    r = await c.get(url, params={"page_size": 200})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


async def test_requester_plus_payment_officer_sees_only_own_prs_and_grs(test_engine):
    w = await _world(test_engine, "payment_officer")
    async with _client(w["me"]) as c:
        prs = await _ids(c, "/api/v1/pr")
        assert str(w["me_pr"]) in prs
        assert str(w["other_pr"]) not in prs, "Payment Officer has no View PR — Own scope must hold"
        assert (await c.get(f"/api/v1/pr/{w['other_pr']}")).status_code == 404
        assert (await c.get(f"/api/v1/pr/{w['me_pr']}")).status_code == 200

        grs = await _ids(c, "/api/v1/gr")
        assert str(w["me_gr"]) in grs
        assert str(w["other_gr"]) not in grs, "Payment Officer has no View GR — Own scope must hold"
        assert (await c.get(f"/api/v1/gr/{w['other_gr']}")).status_code == 404

        # …while what Payment Officer DOES hold stays company-wide: View PO.
        pos = await _ids(c, "/api/v1/po")
        assert {str(w["me_po"]), str(w["other_po"])} <= pos
        assert (await c.get(f"/api/v1/po/{w['other_po']}")).status_code == 200

        # The PR list's Department / Requester pickers follow the PR scope.
        r = await c.get("/api/v1/config/me/scoped-departments")
        assert r.status_code == 200, r.text
        assert r.json()["unrestricted"] is False


async def test_requester_plus_role_holding_view_pr_still_sees_everything(test_engine):
    """Admission counterpart: an unrestricted role that DOES hold View PR / View
    GR (Procurement Officer) still opens every PR and GR."""
    w = await _world(test_engine, "procurement_officer")
    async with _client(w["me"]) as c:
        assert str(w["other_pr"]) in await _ids(c, "/api/v1/pr")
        assert str(w["other_gr"]) in await _ids(c, "/api/v1/gr")
        assert (await c.get(f"/api/v1/pr/{w['other_pr']}")).status_code == 200
        r = await c.get("/api/v1/config/me/scoped-departments")
        assert r.json()["unrestricted"] is True
