"""GET /pr?has_po=false — the approved PRs New PO's Linked PR selector offers
(ISS-0087, PRD §3.12.1 "Linked PR (optional; one PR → one PO)").

has_po=false keeps only PRs no PO has been raised from yet; has_po=true only
those with one; omitted, both. Rows are tagged with a token and the list is
narrowed to them by search, and deleted again afterwards.
"""
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor


@pytest.fixture
async def seeded(test_engine):
    tok = "hp" + uuid.uuid4().hex[:8] + "x"
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        creator = User(email=f"{tok}@t.com", full_name="HasPo", role="system_admin",
                       hashed_password="x", is_active=True)
        vendor = Vendor(code=f"V-{tok}", name="HasPo Vendor", category="Services",
                        contact_name="H", contact_email="h@t.test")
        db.add_all([creator, vendor])
        await db.flush()
        free = PurchaseRequest(number=f"PR-{tok}-FREE", title=f"{tok} free", type=2,
                               status="approved", created_by=creator.id)
        taken = PurchaseRequest(number=f"PR-{tok}-TAKEN", title=f"{tok} taken", type=2,
                                status="approved", created_by=creator.id)
        db.add_all([free, taken])
        await db.flush()
        po = PurchaseOrder(number=f"PO-{tok}", title=f"{tok} po", type=2, status="draft",
                           vendor_id=vendor.id, vendor_name=vendor.name,
                           created_by=creator.id, pr_id=taken.id, pr_number=taken.number)
        db.add(po)
        await db.flush()
        taken.po_id = po.id
        taken.po_number = po.number
        await db.commit()
        ids = {"free": str(free.id), "taken": str(taken.id)}
        rows = [(PurchaseOrder, po.id), (PurchaseRequest, free.id), (PurchaseRequest, taken.id),
                (Vendor, vendor.id), (User, creator.id)]
    yield tok, ids
    async with test_engine.begin() as conn:
        await conn.execute(PurchaseRequest.__table__.update()
                           .where(PurchaseRequest.id == uuid.UUID(ids["taken"])).values(po_id=None))
        for model, pk in rows:
            await conn.execute(delete(model.__table__).where(model.__table__.c.id == pk))


async def _ids(client, **params):
    r = await client.get("/api/v1/pr", params={"status": "approved", **params})
    assert r.status_code == 200, r.text
    return {it["id"] for it in r.json()["items"]}


async def test_has_po_false_lists_only_prs_without_a_po(admin_client, seeded):
    tok, ids = seeded
    assert await _ids(admin_client, search=tok, has_po="false") == {ids["free"]}


async def test_has_po_true_lists_only_prs_with_a_po(admin_client, seeded):
    tok, ids = seeded
    assert await _ids(admin_client, search=tok, has_po="true") == {ids["taken"]}


async def test_without_has_po_both_are_listed(admin_client, seeded):
    tok, ids = seeded
    assert await _ids(admin_client, search=tok) == {ids["free"], ids["taken"]}
