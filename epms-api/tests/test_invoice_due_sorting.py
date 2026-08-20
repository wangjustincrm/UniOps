"""GET /invoices — due-date ordering and the overdue filter.

Both exist for the Invoice List's Due Date column: the list is paginated and
ordered by upload time, so without a server-side sort/filter the column can
show an invoice is overdue only once you have already paged to it.

Every test scopes itself to its own vendor via `search`, because the test DB is
shared with the rest of the suite.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.invoice import Invoice

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"


async def _make_vendor(client, name):
    r = await client.post(VENDOR_URL, json={
        "code": f"VND-DUE-{uuid.uuid4().hex[:8]}", "name": name, "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_invoice(client, vendor_id, due_date):
    r = await client.post(INV_URL, json={
        "vendor_id": vendor_id,
        "vendor_invoice_number": f"VI-{uuid.uuid4().hex[:8]}",
        "amount": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-01-01", "due_date": due_date,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _set_status(test_engine, invoice_id, status):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        inv = (await db.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))).scalar_one()
        inv.status = status
        await db.commit()


async def _refs(client, vendor_name, **params):
    r = await client.get(INV_URL, params={"search": vendor_name, "page_size": 50, **params})
    assert r.status_code == 200, r.text
    return [i["due_date"] for i in r.json()["items"]]


@pytest.mark.asyncio
async def test_sort_by_due_date_returns_the_soonest_first(admin_client):
    name = f"Due Sort Vendor {uuid.uuid4().hex[:8]}"
    v = await _make_vendor(admin_client, name)
    # Created deliberately out of order, so a result that merely echoes
    # insertion order cannot pass.
    for d in ("2026-06-15", "2026-02-01", "2026-04-10"):
        await _make_invoice(admin_client, v["id"], d)

    assert await _refs(admin_client, name, sort="due_date") == [
        "2026-02-01", "2026-04-10", "2026-06-15"
    ]


@pytest.mark.asyncio
async def test_sort_by_due_date_descending(admin_client):
    name = f"Due Sort Desc Vendor {uuid.uuid4().hex[:8]}"
    v = await _make_vendor(admin_client, name)
    for d in ("2026-02-01", "2026-06-15", "2026-04-10"):
        await _make_invoice(admin_client, v["id"], d)

    assert await _refs(admin_client, name, sort="-due_date") == [
        "2026-06-15", "2026-04-10", "2026-02-01"
    ]


@pytest.mark.asyncio
async def test_overdue_filter_keeps_only_unpaid_past_due_invoices(admin_client, test_engine):
    name = f"Overdue Vendor {uuid.uuid4().hex[:8]}"
    v = await _make_vendor(admin_client, name)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    next_year = (date.today() + timedelta(days=365)).isoformat()

    await _make_invoice(admin_client, v["id"], yesterday)
    await _make_invoice(admin_client, v["id"], next_year)
    settled = await _make_invoice(admin_client, v["id"], yesterday)
    # A paid invoice is not chased, however long ago it fell due — the filter
    # must agree with the column's own colouring rule.
    await _set_status(test_engine, settled["id"], "paid")

    assert await _refs(admin_client, name, overdue="true") == [yesterday]


@pytest.mark.asyncio
async def test_due_today_is_not_yet_overdue(admin_client):
    name = f"Due Today Vendor {uuid.uuid4().hex[:8]}"
    v = await _make_vendor(admin_client, name)
    await _make_invoice(admin_client, v["id"], date.today().isoformat())

    assert await _refs(admin_client, name, overdue="true") == []


@pytest.mark.asyncio
async def test_list_without_sort_is_unchanged(admin_client):
    """Newest-first stays the default: every other caller of this endpoint
    (invoice pickers, the PO detail sub-list) relies on it."""
    name = f"Default Order Vendor {uuid.uuid4().hex[:8]}"
    v = await _make_vendor(admin_client, name)
    for d in ("2026-06-15", "2026-02-01"):
        await _make_invoice(admin_client, v["id"], d)

    # Created oldest-due first, so newest-created (2026-02-01) leads.
    assert await _refs(admin_client, name) == ["2026-02-01", "2026-06-15"]
