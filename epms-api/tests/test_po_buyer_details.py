"""Buyer-supplied detail on NC-imported POs (columns, endpoint, authz).

The columns are human-owned: the NC mirror never sources or writes them
(see app/services/nc_purchase_sync/writer.py). Re-sync protection lives in
tests/test_nc_purchase_writer.py.

PATCH /po/{id}/imported-details is deliberately NOT the general PATCH /po/{id}:
that one can replace the vendor, the currency and the entire line-item set, so
widening its status gate for NC POs would let anyone holding epms.po.write
rewrite the money on an order already in the invoice/payment flow. These tests
pin that separation — especially test_cannot_touch_another_pos_line and
test_locked_fields_are_unreachable.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.db.base import Base
from app.main import create_app
from app.models.admin_audit_log import AdminAuditLog
from app.models.invoice import Invoice
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

_KEY = "epms.po.edit_imported"


def test_buyer_detail_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "buyer_notes" in po.c
    assert "incoterms" in po.c
    assert "buyer_edited_at" in po.c
    assert "sample" in Base.metadata.tables["po_line_items"].c


async def _grant_edit_imported(db):
    """conftest's default matrix seeds only phase-1 keys, so the phase-2 key this
    endpoint is gated on has to be inserted here — mirroring what identity's
    0006_po_edit_imported migration does in production."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Edit Imported (NC) POs',107) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('erp_pa_officer','{_KEY}') ON CONFLICT DO NOTHING"))


async def _user(db, role: str):
    return await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=role.replace("_", " ").title(), role=role))


async def _nc_po(db, *, status: str = "issued", source: str | None = "nc", creator_id=None):
    """An NC-mirrored PO with one line, shaped like writer.upsert() leaves it."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="NC order", type=1,
        status=status, currency="CAD", vendor_id=v.id, vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("100.00"), source=source, nc_source_pk=uuid.uuid4().hex,
        notes="nc memo [NC Invoiced]", created_by=creator_id,
    )
    db.add(po)
    await db.flush()
    line = PoLineItem(
        po_id=po.id, description="Widget", material_id="MAT-1", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        sort_order=0, planned_arrival_date=date(2026, 9, 15),
    )
    db.add(line)
    await db.flush()
    return po, line


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _url(po_id):
    return f"/api/v1/po/{po_id}/imported-details"


@pytest.mark.asyncio
async def test_erp_pa_officer_fills_in_buyer_details(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, line_id = po.id, line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "expected_delivery": "2026-09-01",
            "delivery_address": "1 Royal Way",
            "incoterms": "FOB Shanghai",
            "tax_code": "HST13",
            "tax_rate": "0.13",
            "is_prepaid": True,
            "buyer_notes": "Ship in one lot.",
            "lines": [{"id": str(line_id), "supplier_item_id": "SKU-9", "sample": "500 g"}],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incoterms"] == "FOB Shanghai"
    assert body["buyer_notes"] == "Ship in one lot."
    assert body["delivery_address"] == "1 Royal Way"
    assert body["buyer_edited_at"] is not None
    assert body["line_items"][0]["supplier_item_id"] == "SKU-9"
    assert body["line_items"][0]["sample"] == "500 g"
    # planned_arrival_date is NC-synced and untouched by this edit; the API
    # must expose it now that PoLineItemResponse carries the field.
    assert body["line_items"][0]["planned_arrival_date"] == "2026-09-15"
    # tax recomputed off the untouched subtotal
    assert Decimal(body["subtotal"]) == Decimal("100.00")
    assert Decimal(body["tax_amount"]) == Decimal("13.00")
    assert Decimal(body["total"]) == Decimal("113.00")
    # NC's own notes column is untouched — the [NC Invoiced] marker finance
    # reads must survive a buyer edit.
    assert body["notes"] == "nc memo [NC Invoiced]"


@pytest.mark.asyncio
async def test_role_without_the_matrix_key_is_rejected(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)          # granted to erp_pa_officer only
        officer = await _user(db, "erp_pa_officer")
        stranger = await _user(db, "requester")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(stranger) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_system_admin_is_allowed(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        admin = await _user(db, "system_admin")
        po, line = await _nc_po(db, creator_id=admin.id)
        po_id = po.id
        await db.commit()

    async with _client_for(admin) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "DDP Toronto"})
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "DDP Toronto"


@pytest.mark.asyncio
async def test_a_pending_nc_po_is_editable(test_engine):
    """An order still in NC's approval chain is mirrored precisely so its PO PDF
    can be printed and signed off-line — and this detail (Incoterms, sample,
    supplier item id) is what makes that PDF usable. Editing it must therefore
    be open BEFORE approval, not after."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, status="nc_pending", creator_id=officer.id)
        po_id, line_id = po.id, line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "FOB Shanghai",
            "lines": [{"id": str(line_id), "sample": "500 g"}],
        })
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "FOB Shanghai"
    assert r.json()["line_items"][0]["sample"] == "500 g"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["closed", "nc_milk", "nc_pending", "draft",
                                    "partially_received", "cancelled"])
async def test_every_nc_po_is_editable_whatever_its_status(test_engine, status):
    """The status gate is gone on purpose.

    Everything this endpoint writes is buyer-supplied detail the ERP has no
    column for, and the sync already refuses to overwrite it (writer.upsert
    omits those columns once buyer_edited_at is set). So a later NC change
    cannot collide with it, and there is no status at which the buyer stops
    needing to record what NC never held — a closed order still gets asked
    about its Incoterms.

    The one field that IS money — tax_rate — is guarded separately, by whether
    an invoice exists rather than by status. See the tests below.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, status=status, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "EXW"


async def _invoice_for(db, po, uploader_id):
    """An invoice pointing at this PO — what makes it "consumed" downstream."""
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number="V-1",
        vendor_id=po.vendor_id, vendor_name=po.vendor_name,
        amount=Decimal("100.00"), tax_amount=Decimal("0"),
        total_amount=Decimal("100.00"), currency="CAD",
        invoice_date=date(2026, 8, 1), due_date=date(2026, 9, 1),
        status="matched", line_items=[], uploaded_by=uploader_id,
        po_id=po.id, po_number=po.number,
    )
    db.add(inv)
    await db.flush()
    return inv


@pytest.mark.asyncio
async def test_tax_rate_cannot_be_changed_once_an_invoice_exists(test_engine):
    """Changing the rate re-derives tax_amount and total off the same subtotal.
    On a PO an invoice already points at, that silently moves the figure every
    3-way variance was measured against."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        await _invoice_for(db, po, officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"tax_code": "HST13", "tax_rate": "0.13"})
    assert r.status_code == 409, r.text
    assert "invoice" in r.text.lower()

    async with factory() as db:
        fresh = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        assert fresh.tax_rate == Decimal("0")
        assert fresh.total == Decimal("100.00")


@pytest.mark.asyncio
async def test_an_invoiced_po_still_takes_every_other_edit(test_engine):
    """The guard is on the money, not on the document."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        await _invoice_for(db, po, officer.id)
        po_id, line_id = po.id, line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "DDP Toronto",
            "buyer_notes": "Call before delivery.",
            "lines": [{"id": str(line_id), "sample": "500 g"}],
        })
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "DDP Toronto"
    assert r.json()["line_items"][0]["sample"] == "500 g"


@pytest.mark.asyncio
async def test_an_invoiced_po_accepts_a_save_that_leaves_the_rate_alone(test_engine):
    """The edit form re-sends tax_rate on every CAD save, even one that only
    touched Incoterms. Rejecting on the key's PRESENCE would make an invoiced
    CAD order completely unsavable; the guard is on the VALUE changing."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        await _invoice_for(db, po, officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "EXW", "tax_code": None, "tax_rate": "0",
        })
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "EXW"


@pytest.mark.asyncio
async def test_po_detail_reports_whether_an_invoice_exists(test_engine):
    """The edit form disables the tax control off this flag. has_unpaid_invoice
    cannot be used for it: that one is computed by the LIST endpoint only and is
    always False on the detail response."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "system_admin")
        plain, _ = await _nc_po(db, creator_id=officer.id)
        invoiced, _ = await _nc_po(db, creator_id=officer.id)
        await _invoice_for(db, invoiced, officer.id)
        plain_id, invoiced_id = plain.id, invoiced.id
        await db.commit()

    async with _client_for(officer) as c:
        assert (await c.get(f"/api/v1/po/{plain_id}")).json()["has_invoice"] is False
        assert (await c.get(f"/api/v1/po/{invoiced_id}")).json()["has_invoice"] is True


@pytest.mark.asyncio
async def test_non_nc_po_is_rejected(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, source=None, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 409, r.text
    assert "NC-imported" in r.text


@pytest.mark.asyncio
async def test_cannot_touch_another_pos_line(test_engine):
    """Passing a line id that belongs to a different PO must be rejected AND must
    leave that line untouched. Without the ownership check this endpoint would be
    a cross-document write primitive. It must also roll back header fields sent
    in the same rejected request — a 400 that quietly wrote incoterms would be
    a partial write masquerading as a clean rejection."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        mine, _ = await _nc_po(db, creator_id=officer.id)
        _, victim_line = await _nc_po(db, creator_id=officer.id)
        mine_id, victim_line_id = mine.id, victim_line.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(mine_id), json={
            "incoterms": "X",
            "lines": [{"id": str(victim_line_id), "supplier_item_id": "STOLEN"}]})
    assert r.status_code == 400, r.text

    async with factory() as db:
        got = (await db.execute(
            select(PoLineItem.supplier_item_id).where(PoLineItem.id == victim_line_id)
        )).scalar_one()
        assert got is None, "a rejected request must not have written the other PO's line"
        po_incoterms = (await db.execute(
            select(PurchaseOrder.incoterms).where(PurchaseOrder.id == mine_id)
        )).scalar_one()
        assert po_incoterms is None, "a rejected request must not have written header fields either"


@pytest.mark.asyncio
async def test_locked_fields_are_unreachable(test_engine):
    """vendor / currency / title / qty / unit_price are not in the request schema.
    Sending them must change nothing — pydantic drops the unknown keys."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, line_id = po.id, line.id
        before = (po.vendor_id, po.vendor_name, po.currency, po.title, po.subtotal)
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "EXW",
            "vendor_id": str(uuid.uuid4()),
            "currency": "USD",
            "title": "hacked",
            "lines": [{"id": str(line_id), "supplier_item_id": "SKU-1",
                       "qty": "999", "unit_price": "0.01"}],
        })
    assert r.status_code == 200, r.text

    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        assert (po.vendor_id, po.vendor_name, po.currency, po.title, po.subtotal) == before
        ln = (await db.execute(
            select(PoLineItem).where(PoLineItem.id == line_id))).scalar_one()
        assert ln.qty == Decimal("10.0000")
        assert ln.unit_price == Decimal("10.00")
        assert ln.supplier_item_id == "SKU-1"


@pytest.mark.asyncio
async def test_explicit_null_clears_a_field(test_engine):
    """A key present in the body with an explicit JSON null must clear the
    column — this is exactly what the edit page's clear button sends
    (`incoterms || null`, etc.), so treating null the same as "not supplied"
    made a wrongly-entered value impossible to remove."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "FOB Shanghai", "expected_delivery": "2026-09-01",
        })
        assert r.status_code == 200, r.text

        r = await c.patch(_url(po_id), json={
            "incoterms": None, "expected_delivery": None,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incoterms"] is None
    assert body["expected_delivery"] is None


@pytest.mark.asyncio
async def test_omitted_key_leaves_the_stored_value_untouched(test_engine):
    """The mirror image of test_explicit_null_clears_a_field: a key that is
    absent from the body — not sent at all — must not be confused with an
    explicit null. Pinning both behaviours against each other is the point;
    either one alone could pass for the wrong reason."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "FOB Shanghai"})
        assert r.status_code == 200, r.text

        r = await c.patch(_url(po_id), json={"buyer_notes": "unrelated edit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incoterms"] == "FOB Shanghai"
    assert body["buyer_notes"] == "unrelated edit"


@pytest.mark.asyncio
async def test_edit_is_audited(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_edit_imported(db)
        officer = await _user(db, "erp_pa_officer")
        po, line = await _nc_po(db, creator_id=officer.id)
        po_id, po_number = po.id, po.number
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"incoterms": "FOB Shanghai"})
    assert r.status_code == 200, r.text

    async with factory() as db:
        row = (await db.execute(
            select(AdminAuditLog).where(AdminAuditLog.record_id == po_id))).scalar_one()
        assert row.entity == "po"
        assert row.action == "edit"
        assert row.system == "epms"
        assert row.record_number == po_number
        assert row.after["incoterms"] == "FOB Shanghai"
