"""GET /api/v1/freight/shipments — click-to-sort on the paged Shipments list.

Contract: sort=<key>&dir=asc|desc, unknown key -> 422, empties last in both
directions, no sort -> newest registered first (unchanged), sort applied in
SQL before offset/limit so page 2 continues page 1. Each key sorts what the
cell shows (carrier CODE, department name of the cost centre, invoice ref).

Rows are scoped by a unique tracking-number token (`search`) and deleted again
after each test — the test DB is shared with the rest of the suite.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.freight import FreightCarrier, FreightInvoiceLine, FreightShipment
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor

URL = "/api/v1/freight/shipments"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture
async def seeded(test_engine):
    tag = uuid.uuid4().hex[:8].upper()
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ids: dict = {"tag": tag}
    async with sf() as db:
        user = User(id=uuid.uuid4(), email=f"frt{tag.lower()}@example.com",
                    hashed_password=hash_password("x"), full_name="U", role="system_admin",
                    is_active=True)
        vendor = Vendor(id=uuid.uuid4(), code=f"VFS{tag}", name=f"Courier {tag}", category="Logistics",
                        contact_name="B", contact_email="b@example.com", payment_terms="net30")
        vendor2 = Vendor(id=uuid.uuid4(), code=f"VFT{tag}", name=f"Courier2 {tag}", category="Logistics",
                         contact_name="B", contact_email="b@example.com", payment_terms="net30")
        d_q = Department(id=uuid.uuid4(), code=f"Q{tag}", name=f"quality {tag}")
        d_s = Department(id=uuid.uuid4(), code=f"S{tag}", name=f"Supply {tag}")
        db.add_all([user, vendor, vendor2, d_q, d_s])
        await db.flush()
        cc_s2 = CostCenter(id=uuid.uuid4(), code=f"S2-{tag}", name="b", department_id=d_s.id)
        cc_s1 = CostCenter(id=uuid.uuid4(), code=f"S1-{tag}", name="a", department_id=d_s.id)
        cc_q = CostCenter(id=uuid.uuid4(), code=f"Q1-{tag}", name="q", department_id=d_q.id)
        c_ups = FreightCarrier(id=uuid.uuid4(), vendor_id=vendor.id, code=f"U{tag}", name="zz UPS")
        # One carrier per vendor (unique freight_carriers.vendor_id).
        c_fdx = FreightCarrier(id=uuid.uuid4(), vendor_id=vendor2.id, code=f"F{tag}", name="aa FedEx")
        db.add_all([cc_s2, cc_s1, cc_q, c_ups, c_fdx])
        await db.flush()
        spec = [
            # key, carrier, direction, cc, dept, created_by_name, created offset (days)
            ("a", c_ups, "outbound", cc_s2, d_s, "bob", 2),
            ("b", c_fdx, "inbound", cc_q, d_q, None, 0),
            ("c", c_ups, "inbound", cc_s1, d_s, "Amy", 3),
            ("d", c_fdx, "outbound", cc_s1, d_s, "", 1),
        ]
        ship = {}
        for k, car, dirn, cc, dept, by, off in spec:
            s = FreightShipment(id=uuid.uuid4(), carrier_id=car.id, tracking_no=f"SRT{tag}{k.upper()}",
                                direction=dirn, cost_center_id=cc.id, department_id=dept.id,
                                created_by=user.id, created_by_name=by,
                                created_at=T0 + timedelta(days=off))
            db.add(s)
            ship[k] = s
        await db.flush()
        # b and d billed by finalised invoices; d's ref sorts first.
        for k, ref in (("b", f"INV-Z{tag}"), ("d", f"INV-A{tag}")):
            inv = Invoice(id=uuid.uuid4(), internal_ref=f"FRS-{tag}-{k}", vendor_invoice_number=ref,
                          vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("10"),
                          total_amount=Decimal("10"), invoice_date=date(2026, 10, 10),
                          due_date=date(2026, 10, 30), uploaded_by=user.id, status="matched")
            db.add(inv)
            await db.flush()
            db.add(FreightInvoiceLine(invoice_id=inv.id, amount=Decimal("10"),
                                      tracking_no=ship[k].tracking_no, shipment_id=ship[k].id))
        await db.commit()
        ids.update(user=user.id, vendors=[vendor.id, vendor2.id], depts=[d_q.id, d_s.id],
                   ccs=[cc_s2.id, cc_s1.id, cc_q.id], carriers=[c_ups.id, c_fdx.id])
    yield ids
    async with sf() as db:
        inv_ids = [i for (i,) in (await db.execute(
            Invoice.__table__.select().with_only_columns(Invoice.id)
            .where(Invoice.internal_ref.like(f"FRS-{tag}-%")))).all()]
        await db.execute(sa_delete(FreightInvoiceLine).where(FreightInvoiceLine.invoice_id.in_(inv_ids)))
        await db.execute(sa_delete(Invoice).where(Invoice.id.in_(inv_ids)))
        await db.execute(sa_delete(FreightShipment).where(FreightShipment.tracking_no.like(f"SRT{tag}%")))
        await db.execute(sa_delete(FreightCarrier).where(FreightCarrier.id.in_(ids["carriers"])))
        await db.execute(sa_delete(CostCenter).where(CostCenter.id.in_(ids["ccs"])))
        await db.execute(sa_delete(Department).where(Department.id.in_(ids["depts"])))
        await db.execute(sa_delete(Vendor).where(Vendor.id.in_(ids["vendors"])))
        await db.execute(sa_delete(User).where(User.id == ids["user"]))
        await db.commit()


async def _keys(client, tag, **params):
    r = await client.get(URL, params={"search": f"SRT{tag}", "page_size": 50, **params})
    assert r.status_code == 200, r.text
    return [i["tracking_no"][-1].lower() for i in r.json()["items"]]


@pytest.mark.asyncio
async def test_default_order_unchanged(admin_client, seeded):
    assert await _keys(admin_client, seeded["tag"]) == ["c", "a", "d", "b"]   # newest first


@pytest.mark.asyncio
async def test_carrier_sorts_by_shown_code_both_directions(admin_client, seeded):
    t = seeded["tag"]
    # Codes F… (b, d) before U… (a, c); ties fall back to newest first.
    assert await _keys(admin_client, t, sort="carrier") == ["d", "b", "c", "a"]
    assert await _keys(admin_client, t, sort="carrier", dir="desc") == ["c", "a", "d", "b"]


@pytest.mark.asyncio
async def test_department_then_cost_center_code(admin_client, seeded):
    t = seeded["tag"]
    # "quality" < "Supply" case-insensitively; within Supply, S1 (c, d) before S2 (a).
    assert await _keys(admin_client, t, sort="department") == ["b", "c", "d", "a"]
    assert await _keys(admin_client, t, sort="department", dir="desc") == ["a", "c", "d", "b"]


@pytest.mark.asyncio
async def test_empty_values_last_in_both_directions(admin_client, seeded):
    t = seeded["tag"]
    # created_by_name: b is NULL, d is '' — both empty.
    asc = await _keys(admin_client, t, sort="created_by_name")
    desc = await _keys(admin_client, t, sort="created_by_name", dir="desc")
    assert asc[:2] == ["c", "a"] and set(asc[2:]) == {"b", "d"}
    assert desc[:2] == ["a", "c"] and set(desc[2:]) == {"b", "d"}
    # Invoice: only b and d are billed.
    asc = await _keys(admin_client, t, sort="invoice_number")
    desc = await _keys(admin_client, t, sort="invoice_number", dir="desc")
    assert asc[:2] == ["d", "b"] and set(asc[2:]) == {"a", "c"}
    assert desc[:2] == ["b", "d"] and set(desc[2:]) == {"a", "c"}


@pytest.mark.asyncio
async def test_page_two_continues_page_one(admin_client, seeded):
    t = seeded["tag"]
    p = dict(search=f"SRT{t}", sort="created_at", dir="asc", page_size=2)
    r1 = (await admin_client.get(URL, params={**p, "page": 1})).json()
    r2 = (await admin_client.get(URL, params={**p, "page": 2})).json()
    assert r1["total"] == 4
    keys = [i["tracking_no"][-1].lower() for i in r1["items"] + r2["items"]]
    assert keys == ["b", "d", "a", "c"]


@pytest.mark.asyncio
async def test_unknown_sort_key_and_bad_dir_are_422(admin_client):
    assert (await admin_client.get(URL, params={"sort": "carrier_id"})).status_code == 422
    assert (await admin_client.get(URL, params={"sort": "carrier", "dir": "up"})).status_code == 422
