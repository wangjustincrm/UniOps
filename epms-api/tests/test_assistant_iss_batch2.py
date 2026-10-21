"""Assistant fixes from the 2026-10-20 issue review, batch 2: queries that
return the right rows and the right numbers.
"""
import uuid
from datetime import date
from decimal import Decimal

from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor

QUERY = "/api/v1/assistant/query"


def _uid(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _vendor(db, **kw) -> Vendor:
    v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name=kw.pop("name", "Batch2 Vendor"),
               category="Services", contact_name="A", contact_email="a@t.test", **kw)
    db.add(v)
    await db.flush()
    return v


def _po(v, uid, number, **kw) -> PurchaseOrder:
    kw.setdefault("status", "issued")
    kw.setdefault("total", Decimal("100"))
    return PurchaseOrder(number=number, title="t", type=2, vendor_id=v.id,
                         vendor_name=v.name, subtotal=kw["total"], created_by=uid, **kw)


def _inv(v, uid, ref, **kw) -> Invoice:
    amount = kw.pop("amount", Decimal("10"))
    kw.setdefault("status", "matched")
    return Invoice(internal_ref=ref, vendor_invoice_number=ref, vendor_id=v.id,
                   vendor_name=v.name, amount=amount, tax_amount=Decimal("0"),
                   total_amount=amount, invoice_date=date(2026, 10, 1),
                   due_date=date(2026, 11, 1), line_items=[], uploaded_by=uid, **kw)


# ── ISS-0060 / ISS-0058: a one-to-many link never multiplies the parent ──────

async def _po_with_invoices(test_engine, admin_client):
    """PO-A has three matched invoices, PO-B one exception invoice, PO-C none."""
    tag = uuid.uuid4().hex[:6]
    uid = _uid(admin_client)
    async with _factory(test_engine)() as db:
        v = await _vendor(db)
        a, b, c = (_po(v, uid, f"PO-MANY-{tag}-{x}") for x in "ABC")
        db.add_all([a, b, c])
        await db.flush()
        db.add_all([_inv(v, uid, f"INV-MANY-{tag}-A{i}", po_id=a.id, po_number=a.number)
                    for i in range(3)])
        db.add(_inv(v, uid, f"INV-MANY-{tag}-B", po_id=b.id, po_number=b.number,
                    status="exception"))
        await db.commit()
    return tag


async def _query(client, **body):
    r = await client.post(QUERY, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_filter_on_child_rows_counts_each_parent_once(test_engine, admin_client):
    tag = await _po_with_invoices(test_engine, admin_client)
    mine = {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"}

    body = await _query(admin_client, entity="purchase_order", select=["number"],
                        where=[mine, {"field": "invoices.status", "op": "eq",
                                      "value": "matched"}])
    assert [r["number"][-1] for r in body["rows"]] == ["A"], "listed once, not three times"

    body = await _query(admin_client, entity="purchase_order", metrics=["count"],
                        where=[mine, {"field": "invoices.status", "op": "in",
                                      "value": ["matched", "exception"]}])
    assert body["matched_rows"] == 2


async def test_conditions_on_one_link_describe_the_same_child(test_engine, admin_client):
    """matched AND exception on the same invoice is impossible; as separate
    joins it would have matched PO-A and PO-B through different invoices."""
    tag = await _po_with_invoices(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order", select=["number"], where=[
        {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"},
        {"field": "invoices.status", "op": "eq", "value": "matched"},
        {"field": "invoices.internal_ref", "op": "like", "value": f"{tag}-B"},
    ])
    assert body["rows"] == []


async def test_an_empty_child_field_means_no_child(test_engine, admin_client):
    tag = await _po_with_invoices(test_engine, admin_client)
    body = await _query(admin_client, entity="purchase_order", select=["number"], where=[
        {"field": "number", "op": "like", "value": f"PO-MANY-{tag}"},
        {"field": "invoices.internal_ref", "op": "eq", "value": None},
    ])
    assert [r["number"][-1] for r in body["rows"]] == ["C"]


async def test_listing_or_grouping_across_a_child_link_is_refused(admin_client):
    for body in (
        {"entity": "purchase_order", "select": ["number", "invoices.internal_ref"]},
        {"entity": "purchase_order", "group_by": ["invoices.status"], "metrics": ["count"]},
        {"entity": "purchase_order", "select": ["number"],
         "order_by": {"field": "invoices.total_amount"}},
    ):
        r = await admin_client.post(QUERY, json=body)
        assert r.status_code == 422, body
        assert "one purchase_order has many invoice rows" in r.json()["detail"]
    # The other direction is still a plain join.
    r = await admin_client.post(QUERY, json={
        "entity": "invoice", "group_by": ["order.status"], "metrics": ["count"]})
    assert r.status_code == 200, r.text
