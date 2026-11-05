"""PRD INV-004 — one invoice per vendor + vendor invoice #, on every path.

Production had nine duplicate pairs: the only check was the upload form,
comparing against the invoices its user could see, and every second copy was
uploaded by a PO-scoped requester who could not see AP's first one. The rule
now lives in crud.invoice_dedup (every write path) and in the unique index
uq_invoices_vendor_invoice_key (races, and any writer that skips the lookup).
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import invoice_dedup
from app.models.invoice import UQ_VENDOR_INVOICE_KEY, Invoice
from app.models.vendor import Vendor


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _vendor(test_engine, name: str | None = None) -> Vendor:
    async with _factory(test_engine)() as db:
        v = Vendor(code=f"V-{_tag()}", name=name or f"Dedup Vendor {_tag()}", category="Services",
                   contact_name="A", contact_email="a@t.test")
        db.add(v)
        await db.commit()
        return v


async def _uploader(db) -> uuid.UUID:
    from app.crud import user as user_crud
    from app.schemas.auth import RegisterRequest
    u = await user_crud.create(db, RegisterRequest(
        email=f"dd-{_tag()}@example.com", password="TestPass1!", full_name="AP Clerk",
        role="ap_clerk"))
    await db.flush()
    return u.id


async def _row(test_engine, vendor: Vendor, number: str, *, exempt=False, status="paid") -> Invoice:
    """An invoice written straight to the table (as an import or an old row would be)."""
    async with _factory(test_engine)() as db:
        uploader = await _uploader(db)
        inv = Invoice(
            internal_ref=f"INV-DD-{_tag()}", vendor_invoice_number=number,
            vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("10"),
            tax_amount=Decimal("0"), total_amount=Decimal("10"),
            invoice_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
            status=status, line_items=[], dedup_exempt=exempt, uploaded_by=uploader)
        db.add(inv)
        await db.commit()
        return inv


def _body(vendor_id, number):
    return {"vendor_id": str(vendor_id), "vendor_invoice_number": number,
            "amount": "10.00", "tax_amount": "0", "currency": "CAD",
            "invoice_date": "2026-09-02", "due_date": "2026-10-02"}


@pytest.fixture
async def requester_can_upload(test_engine):
    # Production granted requester invoice_upload on 2026-07-15 — the grant that
    # let the duplicates in. Not in the default matrix, and _restore_default_matrix
    # only re-seeds a wiped matrix — it does not take extra grants back.
    async with _factory(test_engine)() as db:
        await db.execute(text("INSERT INTO role_permissions(role_code, permission_key) "
                              "VALUES ('requester', 'invoice_upload') ON CONFLICT DO NOTHING"))
        await db.commit()
    yield
    async with _factory(test_engine)() as db:
        await db.execute(text("DELETE FROM role_permissions "
                              "WHERE role_code = 'requester' AND permission_key = 'invoice_upload'"))
        await db.commit()


# ── the production failure ───────────────────────────────────────────────────

async def test_requester_cannot_reupload_an_invoice_they_cannot_see(
        test_engine, admin_client, requester_client, requester_can_upload):
    vendor = await _vendor(test_engine)
    first = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "58754243"))
    assert first.status_code == 201, first.text
    ref = first.json()["internal_ref"]

    # Precondition of the bug: the requester's list does not contain AP's copy.
    seen = (await requester_client.get("/api/v1/invoices", params={"search": "58754243"})).json()
    assert ref not in [i["internal_ref"] for i in seen["items"]]

    again = await requester_client.post("/api/v1/invoices", json=_body(vendor.id, "58754243"))
    assert again.status_code == 409, again.text
    assert ref in again.json()["detail"]

    # …while the requester can upload (the refusal above is not "requesters can't").
    fresh = await requester_client.post("/api/v1/invoices", json=_body(vendor.id, f"N-{_tag()}"))
    assert fresh.status_code == 201, fresh.text


async def test_live_check_sees_what_the_user_cannot(
        test_engine, admin_client, requester_client, requester_can_upload):
    vendor = await _vendor(test_engine)
    ref = (await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "AB-77"))).json()["internal_ref"]

    r = await requester_client.get("/api/v1/invoices/duplicate-check",
                                   params={"vendor_id": str(vendor.id), "vendor_invoice_number": "ab 77"})
    assert r.status_code == 200, r.text
    assert r.json()["duplicate"] is True and r.json()["existing_ref"] == ref

    r = await requester_client.get("/api/v1/invoices/duplicate-check",
                                   params={"vendor_id": str(vendor.id), "vendor_invoice_number": "AB-78"})
    assert r.json() == {"duplicate": False, "uncheckable": False, "detail": None, "existing_ref": None}

    r = await requester_client.get("/api/v1/invoices/duplicate-check",
                                   params={"vendor_id": str(vendor.id), "vendor_invoice_number": " - "})
    assert r.json()["duplicate"] is False and r.json()["uncheckable"] is True


async def test_live_check_needs_upload_permission(test_engine, requester_client):
    vendor = await _vendor(test_engine)
    r = await requester_client.get("/api/v1/invoices/duplicate-check",
                                   params={"vendor_id": str(vendor.id), "vendor_invoice_number": "X"})
    assert r.status_code == 403


# ── what "the same number" means ─────────────────────────────────────────────

@pytest.mark.parametrize("variant", [
    "INV-00123/A", " inv-00123/a ", "INV00123A", "inv 00123 a", "INV.00123.A",
])
async def test_spelling_variants_are_one_invoice(test_engine, admin_client, variant):
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "INV-00123/A")
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, variant))
    assert r.status_code == 409, (variant, r.text)


@pytest.mark.parametrize("variant", ["000001", "0000001", "1", "0-0-1"])
async def test_leading_zeros_do_not_make_a_new_invoice(test_engine, admin_client, variant):
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "00001")
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, variant))
    assert r.status_code == 409, (variant, r.text)


async def test_different_numbers_and_inner_zeros_are_different_invoices(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "INV-123-A")
    for number in ("INV-123-B", "INV-1230-A", "INV-1023-A", "000010-OCT"):
        r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, number))
        assert r.status_code == 201, (number, r.text)


async def test_a_suffix_lets_a_reused_number_in(test_engine, admin_client):
    """WTC bills each month as 000010; the second month goes in as 000010-OCT."""
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "000010")
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "000010"))
    assert r.status_code == 409
    assert "000010-OCT" in r.json()["detail"]   # the message says how
    assert (await admin_client.post("/api/v1/invoices",
                                    json=_body(vendor.id, "000010-OCT"))).status_code == 201


@pytest.mark.parametrize("number", ["-", "000", " / ", "0-0"])
async def test_a_number_with_no_letter_or_digit_is_refused(test_engine, admin_client, number):
    vendor = await _vendor(test_engine)
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, number))
    assert r.status_code == 422, (number, r.text)
    assert "letters or digits" in r.json()["detail"]


# ── what "the same vendor" means ─────────────────────────────────────────────

async def test_same_number_other_vendor_is_fine(test_engine, admin_client):
    a, b = await _vendor(test_engine), await _vendor(test_engine)
    await _row(test_engine, a, "SHARED-1")
    assert (await admin_client.post("/api/v1/invoices", json=_body(b.id, "SHARED-1"))).status_code == 201


async def test_a_duplicate_vendor_row_with_the_same_name_is_the_same_vendor(test_engine, admin_client):
    """Vendor master holds one supplier twice; uploading against the other row
    must not get the invoice in again."""
    name = f"Linde Canada {_tag()}"
    a, b = await _vendor(test_engine, name), await _vendor(test_engine, f"  {name.upper()} ")
    await _row(test_engine, a, "L-1")
    r = await admin_client.post("/api/v1/invoices", json=_body(b.id, "L-1"))
    assert r.status_code == 409, r.text


async def test_every_status_counts(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    for status in ("unmatched", "matched", "exception", "approved", "paid"):
        await _row(test_engine, vendor, f"S-{status}", status=status)
        r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, f"s-{status}"))
        assert r.status_code == 409, (status, r.text)


# ── edits ────────────────────────────────────────────────────────────────────

async def test_edit_cannot_take_another_invoices_number(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    a = (await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "E-1"))).json()
    b = (await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "E-2"))).json()

    r = await admin_client.patch(f"/api/v1/invoices/{b['id']}", json={"vendor_invoice_number": "e 1"})
    assert r.status_code == 409, r.text
    assert a["internal_ref"] in r.json()["detail"]

    # Its own number, re-saved or re-spelled, is not a clash with itself.
    for same in ("E-2", "e2 "):
        r = await admin_client.patch(f"/api/v1/invoices/{b['id']}", json={"vendor_invoice_number": same})
        assert r.status_code == 200, (same, r.text)
    r = await admin_client.patch(f"/api/v1/invoices/{b['id']}", json={"vendor_invoice_number": "E-3"})
    assert r.status_code == 200, r.text
    assert r.json()["vendor_invoice_number"] == "E-3"

    r = await admin_client.patch(f"/api/v1/invoices/{b['id']}", json={"vendor_invoice_number": "-"})
    assert r.status_code == 422, r.text


# ── rows that were duplicates before the index ───────────────────────────────

async def test_exempt_rows_stay_editable_and_still_block(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "W-10")
    old = await _row(test_engine, vendor, "W-10", exempt=True, status="unmatched")

    r = await admin_client.patch(f"/api/v1/invoices/{old.id}",
                                 json={"vendor_invoice_number": "W-10", "notes": "re-saved"})
    assert r.status_code == 200, r.text
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "W-10"))
    assert r.status_code == 409, r.text


# ── the database half ────────────────────────────────────────────────────────

async def test_the_index_refuses_a_second_row_written_directly(test_engine):
    vendor = await _vendor(test_engine)
    await _row(test_engine, vendor, "DB-1")
    with pytest.raises(IntegrityError) as e:
        await _row(test_engine, vendor, "db 1")
    assert UQ_VENDOR_INVOICE_KEY in str(e.value.orig)
    # An empty key is outside the index (the lookup refuses those instead).
    await _row(test_engine, vendor, "-")
    await _row(test_engine, vendor, "--")


async def test_a_race_past_the_lookup_is_still_a_409(test_engine, admin_client, monkeypatch):
    """Two uploads both pass the lookup before either commits; the index stops
    the second, and the caller gets the same 409, not a 500."""
    vendor = await _vendor(test_engine)

    async def nothing_found(*a, **k):
        return None
    monkeypatch.setattr(invoice_dedup, "find_duplicate", nothing_found)

    assert (await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "RACE-1"))).status_code == 201
    r = await admin_client.post("/api/v1/invoices", json=_body(vendor.id, "race 1"))
    assert r.status_code == 409, r.text
    assert "Duplicate invoice" in r.json()["detail"]


async def test_simultaneous_uploads_store_one(test_engine, admin_client):
    vendor = await _vendor(test_engine)
    results = await asyncio.gather(*[
        admin_client.post("/api/v1/invoices", json=_body(vendor.id, "SIM-1")) for _ in range(4)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and all(c == 409 for c in codes if c != 201), codes
    async with _factory(test_engine)() as db:
        n = (await db.execute(text("SELECT count(*) FROM invoices WHERE vendor_id = :v"),
                              {"v": vendor.id})).scalar_one()
    assert n == 1


# ── Freight uses the same rule ───────────────────────────────────────────────

async def test_freight_lookup_uses_the_same_key(test_engine):
    from app.crud import freight as freight_crud
    vendor = await _vendor(test_engine)
    inv = await _row(test_engine, vendor, "1Z-0099")
    async with _factory(test_engine)() as db:
        assert inv.internal_ref in await freight_crud.find_duplicate_invoice(db, vendor.id, "1z 0099")
        assert await freight_crud.find_duplicate_invoice(db, vendor.id, "1Z-99") is None
        with pytest.raises(freight_crud.FreightError) as e:
            await freight_crud.find_duplicate_invoice(db, vendor.id, "---")
        assert e.value.status == 422
