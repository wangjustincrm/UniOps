"""Click-to-sort on the paged EPMS lists: GET /invoices, /parts, /vendors.

Contract (same in every UniOps list endpoint): `sort=<key>&dir=asc|desc`,
unknown key -> 422, empty values last in BOTH directions, no sort -> the
endpoint's previous default order, and the sort is applied in SQL before
offset/limit so page 2 continues page 1.

Every test scopes itself through `search` with a unique token, because the
test DB is shared with the rest of the suite.
"""
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.invoice import Invoice
from app.models.part import Part
from app.models.user import User
from app.models.vendor import Vendor


_TOKENS: list[str] = []


def _tok() -> str:
    t = f"srt{uuid.uuid4().hex[:10]}"
    _TOKENS.append(t)
    return t


@pytest.fixture(autouse=True)
async def _cleanup(test_engine):
    """Delete every row this test seeded. The DB is shared with the rest of the
    suite, and leftover vendors with short numeric codes sort onto page 1 of
    the vendors' default order, breaking test_vendors.py."""
    yield
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for t in _TOKENS:
            await db.execute(sa_delete(Invoice).where(Invoice.internal_ref.like(f"INV-{t}-%")))
            await db.execute(sa_delete(Part).where(Part.code.like(f"{t.upper()}-%")))
            await db.execute(sa_delete(Vendor).where(Vendor.name.like(f"{t} %")))
            await db.execute(sa_delete(Vendor).where(Vendor.code == f"V{t}"))
            await db.execute(sa_delete(User).where(User.email == f"{t}@example.com"))
        await db.commit()
    _TOKENS.clear()


async def _commit(test_engine, *rows):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for r in rows:
            db.add(r)
            await db.flush()
        await db.commit()


async def _get(client, url, **params):
    r = await client.get(url, params={"page_size": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ── /invoices ────────────────────────────────────────────────────────────────

async def _seed_invoices(test_engine, tok):
    user = User(id=uuid.uuid4(), email=f"{tok}@example.com", hashed_password=hash_password("x"),
                full_name="U", role="system_admin", is_active=True)
    vendor = Vendor(id=uuid.uuid4(), code=f"V{tok}", name=f"V {tok}", category="general",
                    contact_name="N/A", contact_email="v@example.com", payment_terms="net30")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    spec = [
        # ref suffix, vendor name, status, po_total, po_number, created offset (days)
        ("B", f"{tok} beta", "paid", Decimal("200"), f"PO-{tok}-2", 1),
        ("A", f"{tok} Alpha", "match_review", None, None, 3),
        ("C", f"{tok} gamma", "approved", Decimal("50"), "", 2),
        ("D", f"{tok} delta", "unmatched", Decimal("125"), f"PO-{tok}-1", 0),
    ]
    invs = [
        Invoice(id=uuid.uuid4(), internal_ref=f"INV-{tok}-{s}", vendor_invoice_number=f"VI-{s}",
                vendor_id=vendor.id, vendor_name=name, amount=Decimal("10"),
                total_amount=Decimal("10"), invoice_date=date(2026, 1, 1),
                due_date=date(2026, 2, 1), uploaded_by=user.id, status=st,
                po_total=pt, po_number=pn, created_at=t0 + timedelta(days=off))
        for s, name, st, pt, pn, off in spec
    ]
    await _commit(test_engine, user, vendor, *invs)


def _refs(items, tok):
    return [i["internal_ref"].replace(f"INV-{tok}-", "") for i in items]


@pytest.mark.asyncio
async def test_invoices_sort_vendor_name_both_directions_case_insensitive(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    asc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="vendor_name", dir="asc")
    assert _refs(asc, tok) == ["A", "B", "D", "C"]       # Alpha beta delta gamma
    desc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="vendor_name", dir="desc")
    assert _refs(desc, tok) == ["C", "D", "B", "A"]


@pytest.mark.asyncio
async def test_invoices_nulls_and_blanks_last_in_both_directions(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    asc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="po_total", dir="asc")
    assert _refs(asc, tok) == ["C", "D", "B", "A"]       # 50 125 200 then NULL
    desc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="po_total", dir="desc")
    assert _refs(desc, tok) == ["B", "D", "C", "A"]
    # po_number: NULL (A) and '' (C) are both "no PO" and both go last.
    asc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="po_number", dir="asc")
    assert _refs(asc, tok)[:2] == ["D", "B"] and set(_refs(asc, tok)[2:]) == {"A", "C"}
    desc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="po_number", dir="desc")
    assert _refs(desc, tok)[:2] == ["B", "D"] and set(_refs(desc, tok)[2:]) == {"A", "C"}


@pytest.mark.asyncio
async def test_invoices_status_sorts_by_label_not_code(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    asc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="status", dir="asc")
    # Approved, Paid, Pending Review (match_review), Unmatched — by code
    # match_review would come before paid.
    assert _refs(asc, tok) == ["C", "B", "A", "D"]


@pytest.mark.asyncio
async def test_invoices_upload_date_and_default_order(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    default = await _get(admin_client, "/api/v1/invoices", search=tok)
    assert _refs(default, tok) == ["A", "C", "B", "D"]   # newest uploaded first, unchanged
    asc = await _get(admin_client, "/api/v1/invoices", search=tok, sort="uploaded_at", dir="asc")
    assert _refs(asc, tok) == ["D", "B", "C", "A"]
    # SLA = time since upload: ascending = least elapsed = newest upload.
    sla = await _get(admin_client, "/api/v1/invoices", search=tok, sort="sla", dir="asc")
    assert _refs(sla, tok) == ["A", "C", "B", "D"]


@pytest.mark.asyncio
async def test_invoices_legacy_due_date_forms_still_work(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    # All four share a due date -> tiebreak is the old created_at desc.
    legacy = await _get(admin_client, "/api/v1/invoices", search=tok, sort="-due_date")
    new = await _get(admin_client, "/api/v1/invoices", search=tok, sort="due_date", dir="desc")
    assert _refs(legacy, tok) == _refs(new, tok) == ["A", "C", "B", "D"]


@pytest.mark.asyncio
async def test_invoices_unknown_sort_key_is_422(admin_client):
    r = await admin_client.get("/api/v1/invoices", params={"sort": "id; drop table invoices"})
    assert r.status_code == 422
    r = await admin_client.get("/api/v1/invoices", params={"sort": "vendor_name", "dir": "up"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invoices_page_two_continues_page_one(admin_client, test_engine):
    tok = _tok()
    await _seed_invoices(test_engine, tok)
    p = dict(search=tok, sort="vendor_name", dir="desc", page_size=2)
    r1 = await admin_client.get("/api/v1/invoices", params={**p, "page": 1})
    r2 = await admin_client.get("/api/v1/invoices", params={**p, "page": 2})
    assert r1.json()["total"] == 4
    assert _refs(r1.json()["items"], tok) + _refs(r2.json()["items"], tok) == ["C", "D", "B", "A"]


# ── /parts ───────────────────────────────────────────────────────────────────

async def _seed_parts(test_engine, tok):
    spec = [
        # code suffix, name, price, supplier_part_no, active
        ("2", f"{tok} widget", Decimal("9.50"), "SP-B", True),
        ("1", f"{tok} Bolt", Decimal("120.00"), "", False),
        ("3", f"{tok} anchor", Decimal("30.00"), "sp-a", True),
    ]
    await _commit(test_engine, *[
        Part(code=f"{tok.upper()}-{c}", category="Cat", name=n, supplier="Sup",
             supplier_part_no=spn, unit_price=pr, unit="EA", is_active=act)
        for c, n, pr, spn, act in spec
    ])


def _codes(items, tok):
    return [i["code"].replace(f"{tok.upper()}-", "") for i in items]


@pytest.mark.asyncio
async def test_parts_sort_price_and_name(admin_client, test_engine):
    tok = _tok()
    await _seed_parts(test_engine, tok)
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="unit_price"), tok) == ["2", "3", "1"]
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="unit_price", dir="desc"), tok) == ["1", "3", "2"]
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="name"), tok) == ["3", "1", "2"]


@pytest.mark.asyncio
async def test_parts_blank_last_both_directions_and_status_label(admin_client, test_engine):
    tok = _tok()
    await _seed_parts(test_engine, tok)
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="supplier_part_no"), tok) == ["3", "2", "1"]
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="supplier_part_no", dir="desc"), tok) == ["2", "3", "1"]
    # "Active" < "Inactive"; ties fall back to code.
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok, sort="status"), tok) == ["2", "3", "1"]


@pytest.mark.asyncio
async def test_parts_default_order_unknown_key_and_paging(admin_client, test_engine):
    tok = _tok()
    await _seed_parts(test_engine, tok)
    assert _codes(await _get(admin_client, "/api/v1/parts", search=tok), tok) == ["1", "2", "3"]
    r = await admin_client.get("/api/v1/parts", params={"sort": "image_data_url"})
    assert r.status_code == 422
    p = dict(search=tok, sort="unit_price", dir="desc", page_size=2)
    r1 = (await admin_client.get("/api/v1/parts", params={**p, "page": 1})).json()
    r2 = (await admin_client.get("/api/v1/parts", params={**p, "page": 2})).json()
    assert r1["total"] == 3
    assert _codes(r1["items"], tok) + _codes(r2["items"], tok) == ["1", "3", "2"]


# ── /vendors ─────────────────────────────────────────────────────────────────

async def _seed_vendors(test_engine, tok):
    six = "9" + "".join(random.choices("0123456789", k=5))      # 6 digits
    seven = "1" + "".join(random.choices("0123456789", k=6))    # 7 digits
    alpha = f"Z{tok.upper()}"
    spec = [
        # key, code, erp_id, terms, contact_name, active
        ("six", six, None, "net120", "zed", True),
        ("seven", seven, "E-2", "cod", "", True),
        ("alpha", alpha, "E-1", "net30", "Amy", False),
        ("x", f"Y{tok.upper()}", "", "prepayment", "bob", True),
    ]
    await _commit(test_engine, *[
        Vendor(id=uuid.uuid4(), code=code, erp_id=erp, name=f"{tok} {k}", category="general",
               contact_name=cn, contact_email=f"{k}@example.com", payment_terms=terms,
               is_active=act)
        for k, code, erp, terms, cn, act in spec
    ])


def _keys(items, tok):
    return [i["name"].replace(f"{tok} ", "") for i in items]


@pytest.mark.asyncio
async def test_vendors_code_is_number_aware(admin_client, test_engine):
    tok = _tok()
    await _seed_vendors(test_engine, tok)
    assert _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="code"), tok) == ["six", "seven", "x", "alpha"]
    assert _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="code", dir="desc"), tok) == ["alpha", "x", "seven", "six"]


@pytest.mark.asyncio
async def test_vendors_payment_terms_sort_like_their_labels(admin_client, test_engine):
    tok = _tok()
    await _seed_vendors(test_engine, tok)
    # COD, Net 30, Net 120, Prepayment — plain text would put net120 before net30.
    assert _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="payment_terms"), tok) == ["seven", "alpha", "six", "x"]
    assert _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="payment_terms", dir="desc"), tok) == ["x", "six", "alpha", "seven"]


@pytest.mark.asyncio
async def test_vendors_erp_id_empty_last_both_directions_and_contact_fallback(admin_client, test_engine):
    tok = _tok()
    await _seed_vendors(test_engine, tok)
    asc = _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="erp_id"), tok)
    desc = _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="erp_id", dir="desc"), tok)
    assert asc[:2] == ["alpha", "seven"] and set(asc[2:]) == {"six", "x"}
    assert desc[:2] == ["seven", "alpha"] and set(desc[2:]) == {"six", "x"}
    # Contact: blank name falls back to the email ("seven@example.com").
    assert _keys(await _get(admin_client, "/api/v1/vendors", search=tok, sort="contact"), tok) == ["alpha", "x", "seven", "six"]


@pytest.mark.asyncio
async def test_vendors_inactive_only_filter_default_order_422_and_paging(admin_client, test_engine):
    tok = _tok()
    await _seed_vendors(test_engine, tok)
    r = await admin_client.get("/api/v1/vendors", params={"search": tok, "inactive_only": "true"})
    assert r.json()["total"] == 1 and _keys(r.json()["items"], tok) == ["alpha"]
    # Default (no sort) stays plain text order of code.
    default = _keys(await _get(admin_client, "/api/v1/vendors", search=tok), tok)
    assert default == ["seven", "six", "x", "alpha"]
    assert (await admin_client.get("/api/v1/vendors", params={"sort": "notes"})).status_code == 422
    p = dict(search=tok, sort="payment_terms", dir="asc", page_size=5)
    # page_size floor is 5; four rows fit one page, so split with two queries
    # at the crud level instead: page 2 of size 2 must continue page 1.
    from app.crud import vendor as vendor_crud
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        a, total = await vendor_crud.get_all(db, search=tok, sort="payment_terms", dir="asc", page=1, page_size=2)
        b, _ = await vendor_crud.get_all(db, search=tok, sort="payment_terms", dir="asc", page=2, page_size=2)
    assert total == 4
    assert [v.name.replace(f"{tok} ", "") for v in a + b] == ["seven", "alpha", "six", "x"]
    assert _keys(await _get(admin_client, "/api/v1/vendors", **p), tok) == ["seven", "alpha", "six", "x"]
