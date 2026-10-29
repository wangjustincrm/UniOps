"""Deleting an invoice voids its Finance AP row, or nothing is deleted.

Data Maintenance deleted invoices without telling Finance; by 2026-10-21 five
AP rows pointed at deleted invoices (one of them Sprim's 8,400.02, carried
twice), and their vendor + number blocked the real invoice's own AP row.
Invoice references were also handed out again after a delete, so the orphan
looked like the live invoice's (INV-2026-0653, INV-2026-0618).
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.services import finance_client, finance_sync


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _vendor(test_engine) -> Vendor:
    async with _factory(test_engine)() as db:
        v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name=f"Del Vendor {uuid.uuid4().hex[:8]}",
                   category="Services", contact_name="A", contact_email="a@t.test")
        db.add(v)
        await db.commit()
        return v


async def _upload(client, vendor, number=None) -> dict:
    r = await client.post("/api/v1/invoices", json={
        "vendor_id": str(vendor.id), "vendor_invoice_number": number or f"D-{uuid.uuid4().hex[:8]}",
        "amount": "10.00", "tax_amount": "0", "currency": "CAD",
        "invoice_date": "2026-09-02", "due_date": "2026-10-02"})
    assert r.status_code == 201, r.text
    return r.json()


async def _exists(test_engine, invoice_id) -> bool:
    async with _factory(test_engine)() as db:
        return (await db.get(Invoice, uuid.UUID(invoice_id))) is not None


@pytest.fixture
def finance_refuses(monkeypatch):
    async def _down(*, payload, bearer_token):
        raise finance_client.FinanceSyncError("finance-api unreachable: ConnectError")
    monkeypatch.setattr(finance_client, "upsert_ap_invoice_strict", _down)


# ── every delete path tells Finance ──────────────────────────────────────────

async def test_invoices_page_delete_voids_in_finance(test_engine, admin_client, finance_voids):
    inv = await _upload(admin_client, await _vendor(test_engine))
    r = await admin_client.delete(f"/api/v1/invoices/{inv['id']}")
    assert r.status_code == 204, r.text
    assert [(p["source_invoice_id"], p["status"], p["source_ref"]) for p in finance_voids] == [
        (inv["id"], "void", inv["internal_ref"])]
    assert not await _exists(test_engine, inv["id"])


async def test_data_maintenance_delete_voids_in_finance(test_engine, admin_client, finance_voids):
    """The path that produced every orphan: it told Finance nothing."""
    inv = await _upload(admin_client, await _vendor(test_engine))
    r = await admin_client.delete(f"/api/v1/admin/invoice/{inv['id']}")
    assert r.status_code == 200, r.text
    assert [(p["source_invoice_id"], p["status"]) for p in finance_voids] == [(inv["id"], "void")]
    assert not await _exists(test_engine, inv["id"])


async def test_data_maintenance_bulk_delete_voids_each(test_engine, admin_client, finance_voids):
    vendor = await _vendor(test_engine)
    a, b = await _upload(admin_client, vendor), await _upload(admin_client, vendor)
    r = await admin_client.post("/api/v1/admin/invoice/bulk-delete", json={"ids": [a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    assert sorted(p["source_invoice_id"] for p in finance_voids) == sorted([a["id"], b["id"]])


async def test_data_maintenance_preview_tells_finance_nothing(test_engine, admin_client, finance_voids):
    inv = await _upload(admin_client, await _vendor(test_engine))
    r = await admin_client.delete(f"/api/v1/admin/invoice/{inv['id']}", params={"preview": 1})
    assert r.status_code == 200 and r.json()["preview"] is True
    assert finance_voids == [] and await _exists(test_engine, inv["id"])


# ── Finance refusing means nothing is deleted ────────────────────────────────

async def test_invoices_page_delete_is_refused_when_finance_cannot_void(
        test_engine, admin_client, finance_refuses):
    inv = await _upload(admin_client, await _vendor(test_engine))
    r = await admin_client.delete(f"/api/v1/invoices/{inv['id']}")
    assert r.status_code == 502, r.text
    assert inv["internal_ref"] in r.json()["detail"] and "nothing was deleted" in r.json()["detail"]
    assert await _exists(test_engine, inv["id"])


async def test_data_maintenance_delete_is_refused_when_finance_cannot_void(
        test_engine, admin_client, finance_refuses):
    inv = await _upload(admin_client, await _vendor(test_engine))
    r = await admin_client.delete(f"/api/v1/admin/invoice/{inv['id']}")
    assert r.status_code == 502, r.text
    assert await _exists(test_engine, inv["id"])
    async with _factory(test_engine)() as db:      # and no audit row claims it was deleted
        n = (await db.execute(text(
            "SELECT count(*) FROM admin_audit_log WHERE record_id = :i AND action = 'delete'"),
            {"i": inv["id"]})).scalar_one()
    assert n == 0


async def test_a_delete_refused_before_finance_is_not_voided(test_engine, admin_client, finance_voids):
    """A delete EPMS itself refuses must never reach Finance — Finance would
    void a payable whose invoice is still there."""
    inv = await _upload(admin_client, await _vendor(test_engine))
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE invoices SET status = 'paid' WHERE id = :i"), {"i": inv["id"]})
        await db.commit()
    r = await admin_client.delete(f"/api/v1/invoices/{inv['id']}")
    assert r.status_code == 409, r.text
    assert finance_voids == [] and await _exists(test_engine, inv["id"])


# ── a delete path that forgets is refused at commit ──────────────────────────

async def test_a_request_session_cannot_commit_an_unvoided_delete(test_engine):
    vendor = await _vendor(test_engine)
    async with _factory(test_engine)() as db:
        uid = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar_one()
        inv = Invoice(internal_ref=f"INV-UV-{uuid.uuid4().hex[:6]}", vendor_invoice_number="UV-1",
                      vendor_id=vendor.id, vendor_name=vendor.name, amount=10, tax_amount=0,
                      total_amount=10, invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
                      status="unmatched", line_items=[], uploaded_by=uid)
        db.add(inv)
        await db.commit()

    async with _factory(test_engine)() as db:
        db.info[finance_sync.STRICT] = True             # as get_session sets it
        await db.delete(await db.get(Invoice, inv.id))
        with pytest.raises(RuntimeError, match="without voiding them in Finance"):
            await db.commit()
        await db.rollback()

    async with _factory(test_engine)() as db:           # still there
        assert await db.get(Invoice, inv.id) is not None


# ── references are never issued twice ────────────────────────────────────────

async def test_deleting_the_newest_invoice_does_not_free_its_number(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    newest = await _upload(admin_client, vendor)
    assert (await admin_client.delete(f"/api/v1/invoices/{newest['id']}")).status_code == 204
    after = await _upload(admin_client, vendor)
    assert after["internal_ref"] != newest["internal_ref"]
    prefix = newest["internal_ref"].rsplit("-", 1)[0] + "-"
    assert int(after["internal_ref"][len(prefix):]) == int(newest["internal_ref"][len(prefix):]) + 1


async def test_counter_never_goes_backwards_below_existing_documents(test_engine, admin_client):
    """An invoice written with a higher number than the counter remembers
    (an import, a script) still wins: max(existing, remembered) + 1."""
    vendor = await _vendor(test_engine)
    first = await _upload(admin_client, vendor)
    prefix = first["internal_ref"].rsplit("-", 1)[0] + "-"
    async with _factory(test_engine)() as db:
        uid = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar_one()
        db.add(Invoice(internal_ref=f"{prefix}9990", vendor_invoice_number=f"HI-{uuid.uuid4().hex[:6]}",
                       vendor_id=vendor.id, vendor_name=vendor.name, amount=10, tax_amount=0,
                       total_amount=10, invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
                       status="unmatched", line_items=[], uploaded_by=uid))
        await db.commit()
    nxt = await _upload(admin_client, vendor)
    assert nxt["internal_ref"] == f"{prefix}9991"


async def test_simultaneous_uploads_get_distinct_references(test_engine, admin_client):
    """Different invoices uploaded at the same moment. The old allocator read
    MAX + 1 with no lock: two uploads could take the same reference and one
    got a 500 (seen on 36f6c595's suite: INV-2026-0209 taken twice)."""
    import asyncio
    vendors = [await _vendor(test_engine) for _ in range(6)]
    results = await asyncio.gather(*[_upload_raw(admin_client, v) for v in vendors])
    assert [r.status_code for r in results] == [201] * 6, [r.text for r in results if r.status_code != 201]
    refs = [r.json()["internal_ref"] for r in results]
    assert len(set(refs)) == 6


async def _upload_raw(client, vendor):
    return await client.post("/api/v1/invoices", json={
        "vendor_id": str(vendor.id), "vendor_invoice_number": f"C-{uuid.uuid4().hex[:8]}",
        "amount": "10.00", "tax_amount": "0", "currency": "CAD",
        "invoice_date": "2026-09-02", "due_date": "2026-10-02"})
