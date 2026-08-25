"""Buyer-added line items on an NC-imported PO.

Some suppliers require the tooling/mould quote to appear as a line on the PO
they sign. It is a one-off charge with no material code, so it cannot be set up
in NC's order at all — it has to be added on the UniOps side of the mirror.

That makes these lines different in kind from every other field this endpoint
writes. Incoterms and Sample are inert: NC has no column for them, so a re-sync
cannot disagree. A line carrying money is not inert — it moves the PO's
subtotal, and the sync overwrites the header money from NC on every run. Most of
what is pinned here is that collision being handled rather than avoided:

  * the header is recomputed as NC's own subtotal PLUS the manual lines, in the
    endpoint and again in the writer, so a re-sync restores the same figure
    instead of erasing the added charge;
  * a full reload preserves any PO carrying manual lines — without that, the
    reload deletes the PO and its lines and the charge is gone for good;
  * the lines are entered PRE-TAX, matching `subtotal`, which is what the header
    arithmetic and the invoice variance are measured in. (Measured against NC:
    1,699 of 1,709 in-scope orders carry no tax at all, so the distinction is
    invisible on all but four of them — which is exactly why it needs a test
    rather than an assumption.)
  * they are refused once an invoice points at the PO, on the same evidence the
    tax rate is frozen on.
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
from app.main import create_app
from app.models.invoice import Invoice
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

_KEY = "epms.po.edit_imported"


async def _grant(db):
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Edit Imported (NC) POs',107) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('erp_pa_officer','{_KEY}') ON CONFLICT DO NOTHING"))


async def _user(db, role="erp_pa_officer"):
    return await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Officer", role=role))


async def _nc_po(db, *, tax_rate=Decimal("0"), creator_id=None):
    """One NC line: 10 x 10.00 = 100.00 pre-tax."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    tax = (Decimal("100.00") * tax_rate).quantize(Decimal("0.01"))
    po = PurchaseOrder(
        number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=v.id, vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=tax_rate, tax_amount=tax,
        total=Decimal("100.00") + tax, source="nc", nc_source_pk=uuid.uuid4().hex,
        notes="nc memo", created_by=creator_id,
    )
    db.add(po)
    await db.flush()
    line = PoLineItem(
        po_id=po.id, description="Widget", material_id="MAT-1", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        sort_order=0, nc_source_pk=f"OL-{uuid.uuid4().hex[:8]}",
        planned_arrival_date=date(2026, 9, 15),
    )
    db.add(line)
    await db.flush()
    return po, line


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test", headers={"Authorization": f"Bearer {token}"})


def _url(po_id):
    return f"/api/v1/po/{po_id}/imported-details"


_TOOLING = {"description": "Mould tooling (one-off)", "qty": "1",
            "unit": "EA", "unit_price": "5000.00"}


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _setup(engine, **po_kw):
    factory = _factory(engine)
    async with factory() as db:
        await _grant(db)
        officer = await _user(db)
        po, line = await _nc_po(db, creator_id=officer.id, **po_kw)
        ids = (po.id, line.id)
        await db.commit()
    return officer, ids


async def _reload(engine, po_id):
    factory = _factory(engine)
    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        lines = (await db.execute(
            select(PoLineItem).where(PoLineItem.po_id == po_id)
            .order_by(PoLineItem.sort_order))).scalars().all()
        return po, lines


# ── add / edit / delete ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_manual_line_is_added_and_marked_as_not_nc_sourced(test_engine):
    officer, (po_id, _) = await _setup(test_engine)

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
    assert r.status_code == 200, r.text

    po, lines = await _reload(test_engine, po_id)
    assert len(lines) == 2
    added = lines[-1]
    assert added.description == "Mould tooling (one-off)"
    assert added.line_total == Decimal("5000.00")
    # The discriminator the sync, the writer and the UI all read.
    assert added.nc_source_pk is None
    # No material code, by construction — that is what keeps a one-off charge
    # out of MRP's in-transit supply, which only counts lines that have one.
    assert added.material_id is None
    assert added.sort_order > lines[0].sort_order


@pytest.mark.asyncio
async def test_adding_a_line_raises_the_header_money(test_engine):
    officer, (po_id, _) = await _setup(test_engine, tax_rate=Decimal("0.13"))

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
    assert r.status_code == 200, r.text

    body = r.json()
    assert Decimal(body["subtotal"]) == Decimal("5100.00")
    assert Decimal(body["tax_amount"]) == Decimal("663.00")     # 5100 * 0.13
    assert Decimal(body["total"]) == Decimal("5763.00")


@pytest.mark.asyncio
async def test_editing_a_manual_line_moves_the_money_with_it(test_engine):
    officer, (po_id, _) = await _setup(test_engine)
    async with _client_for(officer) as c:
        first = await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
        added_id = [ln for ln in first.json()["line_items"]
                    if ln["description"].startswith("Mould")][0]["id"]

        r = await c.patch(_url(po_id), json={"manual_lines": [
            {**_TOOLING, "id": added_id, "unit_price": "4000.00"}]})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["subtotal"]) == Decimal("4100.00")

    _, lines = await _reload(test_engine, po_id)
    assert len(lines) == 2, "an edit must not duplicate the line"


@pytest.mark.asyncio
async def test_a_manual_line_left_out_of_the_list_is_deleted(test_engine):
    officer, (po_id, _) = await _setup(test_engine)
    async with _client_for(officer) as c:
        await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
        r = await c.patch(_url(po_id), json={"manual_lines": []})
    assert r.status_code == 200, r.text

    po, lines = await _reload(test_engine, po_id)
    assert len(lines) == 1
    assert lines[0].nc_source_pk is not None
    assert po.subtotal == Decimal("100.00"), "the money must come back down"


@pytest.mark.asyncio
async def test_omitting_the_key_leaves_manual_lines_untouched(test_engine):
    """Same absent-key contract as every other field on this endpoint: a save
    that only changes Incoterms must not wipe the added lines."""
    officer, (po_id, _) = await _setup(test_engine)
    async with _client_for(officer) as c:
        await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
        r = await c.patch(_url(po_id), json={"incoterms": "EXW"})
    assert r.status_code == 200, r.text

    po, lines = await _reload(test_engine, po_id)
    assert len(lines) == 2
    assert po.subtotal == Decimal("5100.00")


# ── what a manual line may not do ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_nc_line_cannot_be_rewritten_through_the_manual_list(test_engine):
    """The whole point of the split: NC owns its lines' description, quantity
    and price. Passing an NC line's id here would be a way around that."""
    officer, (po_id, nc_line_id) = await _setup(test_engine)

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [
            {**_TOOLING, "id": str(nc_line_id), "unit_price": "999999.00"}]})
    assert r.status_code == 400, r.text

    po, lines = await _reload(test_engine, po_id)
    assert lines[0].unit_price == Decimal("10.00")
    assert po.subtotal == Decimal("100.00")


@pytest.mark.asyncio
async def test_a_line_id_from_another_po_is_refused(test_engine):
    officer, (po_id, _) = await _setup(test_engine)
    _, (other_po_id, other_line_id) = await _setup(test_engine)

    async with _client_for(officer) as c:
        other = await c.patch(_url(other_po_id), json={"manual_lines": [_TOOLING]})
        stolen = [ln for ln in other.json()["line_items"]
                  if ln["description"].startswith("Mould")][0]["id"]
        r = await c.patch(_url(po_id), json={"manual_lines": [{**_TOOLING, "id": stolen}]})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"qty": "0"}, {"qty": "-1"}, {"description": ""}, {"description": "   "},
])
async def test_a_meaningless_line_is_rejected(test_engine, bad):
    officer, (po_id, _) = await _setup(test_engine)

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [{**_TOOLING, **bad}]})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_negative_unit_price_is_allowed(test_engine):
    """Credits and one-off discounts are real lines — the same allowance the
    native PO/PR forms already make."""
    officer, (po_id, _) = await _setup(test_engine)

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [
            {**_TOOLING, "description": "Tooling credit", "unit_price": "-250.00"}]})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["subtotal"]) == Decimal("-150.00")


@pytest.mark.asyncio
async def test_a_material_code_cannot_be_smuggled_onto_a_manual_line(test_engine):
    """The field does not exist on the request model, so an extra key is inert.
    If it ever became reachable the line would start counting as MRP supply."""
    officer, (po_id, _) = await _setup(test_engine)

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [
            {**_TOOLING, "material_id": "MAT-999"}]})
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        _, lines = await _reload(test_engine, po_id)
        assert all(ln.material_id != "MAT-999" for ln in lines)


# ── the invoice lock ─────────────────────────────────────────────────────────

async def _invoice_for(db, po, uploader_id):
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
async def test_a_line_cannot_be_added_once_an_invoice_exists(test_engine):
    factory = _factory(test_engine)
    async with factory() as db:
        await _grant(db)
        officer = await _user(db)
        po, _ = await _nc_po(db, creator_id=officer.id)
        await _invoice_for(db, po, officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
    assert r.status_code == 409, r.text
    assert "invoice" in r.text.lower()

    po, lines = await _reload(test_engine, po_id)
    assert len(lines) == 1
    assert po.subtotal == Decimal("100.00")


@pytest.mark.asyncio
async def test_an_invoiced_po_accepts_a_save_that_resends_the_same_lines(test_engine):
    """The form re-sends the whole manual set on every save. Refusing on the
    key's presence would make an invoiced PO unsavable — the same trap the tax
    guard had to avoid."""
    factory = _factory(test_engine)
    async with factory() as db:
        await _grant(db)
        officer = await _user(db)
        po, _ = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        first = await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})
        added = [ln for ln in first.json()["line_items"]
                 if ln["description"].startswith("Mould")][0]

    factory = _factory(test_engine)
    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        await _invoice_for(db, po, officer.id)
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={
            "incoterms": "EXW",
            "manual_lines": [{**_TOOLING, "id": added["id"]}],
        })
    assert r.status_code == 200, r.text
    assert r.json()["incoterms"] == "EXW"


@pytest.mark.asyncio
async def test_an_invoiced_po_refuses_a_deletion_too(test_engine):
    factory = _factory(test_engine)
    async with factory() as db:
        await _grant(db)
        officer = await _user(db)
        po, _ = await _nc_po(db, creator_id=officer.id)
        po_id = po.id
        await db.commit()

    async with _client_for(officer) as c:
        await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})

    factory = _factory(test_engine)
    async with factory() as db:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        await _invoice_for(db, po, officer.id)
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.patch(_url(po_id), json={"manual_lines": []})
    assert r.status_code == 409, r.text
    _, lines = await _reload(test_engine, po_id)
    assert len(lines) == 2


# ── what the UI needs to tell the two kinds apart ────────────────────────────

@pytest.mark.asyncio
async def test_the_response_marks_which_lines_came_from_nc(test_engine):
    officer, (po_id, _) = await _setup(test_engine)

    async with _client_for(officer) as c:
        body = (await c.patch(_url(po_id), json={"manual_lines": [_TOOLING]})).json()

    by_desc = {ln["description"]: ln for ln in body["line_items"]}
    assert by_desc["Widget"]["nc_sourced"] is True
    assert by_desc["Mould tooling (one-off)"]["nc_sourced"] is False


# ── surviving the sync (psycopg2 / writer + service level) ───────────────────

def _nc_payload(seeded_vendor, *, subtotal=Decimal("100.00")):
    """The shape transform.transform() emits, cut down to one order + one line."""
    vid, vname = seeded_vendor
    return {
        "orders": [{
            "nc_source_pk": "O1", "number": "PO-NC-O1", "title": "PO-NC-O1",
            "type": 1, "status": "issued", "source": "nc", "currency": "CAD",
            "total": subtotal, "subtotal": subtotal,
            "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
            "vendor_id": vid, "vendor_name": vname, "pr_id": None,
            "place_order_method": "nc", "place_order_reference": "PO-NC-O1",
            "notes": "nc order",
        }],
        "order_lines": [{
            "nc_source_pk": "OL1", "po_nc_pk": "O1", "material_id": "MAT-1",
            "description": "Widget", "qty": Decimal("10"), "unit": "EA",
            "unit_price": Decimal("10.00"), "line_total": subtotal,
            "received_qty": Decimal("0"), "planned_arrival_date": date(2026, 5, 5),
            "sort_order": 1,
        }],
        "grs": [], "gr_lines": [], "skipped_no_vendor": [],
    }


def _add_manual_line(cur, nc_pk, amount=Decimal("5000.00")):
    cur.execute("select id from purchase_orders where nc_source_pk=%s and source='nc'",
                (nc_pk,))
    po_id = cur.fetchone()[0]
    cur.execute(
        "insert into po_line_items (id,po_id,description,qty,unit,unit_price,"
        "line_total,received_qty,sort_order,nc_source_pk) "
        "values (%s,%s,'Mould tooling',1,'EA',%s,%s,0,99,NULL)",
        (uuid.uuid4(), po_id, amount, amount))
    cur.execute("update purchase_orders set subtotal=subtotal+%s, total=total+%s "
                "where id=%s", (amount, amount, po_id))
    return po_id


def test_a_resync_keeps_the_manual_line_and_its_money(pg_cur, seeded_vendor, system_user_id):
    """The writer rewrites the header money from NC on every run. Without the
    manual-line term it would silently erase a 5,000 tooling charge while
    leaving the line itself on screen — the header and its own lines would stop
    adding up."""
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)
    po_id = _add_manual_line(pg_cur, "O1")

    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)

    pg_cur.execute("select subtotal, total from purchase_orders where id=%s", (po_id,))
    subtotal, total = pg_cur.fetchone()
    assert subtotal == Decimal("5100.00")
    assert total == Decimal("5100.00")
    pg_cur.execute("select count(*) from po_line_items where po_id=%s and nc_source_pk is null",
                   (po_id,))
    assert pg_cur.fetchone()[0] == 1


def test_a_resync_follows_nc_when_nc_itself_changed(pg_cur, seeded_vendor, system_user_id):
    """The manual term is added to NC's NEW figure, not to the stored one —
    otherwise the charge would compound on every single sync."""
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)
    po_id = _add_manual_line(pg_cur, "O1")

    writer.upsert(pg_cur, _nc_payload(seeded_vendor, subtotal=Decimal("250.00")),
                  system_user_id)
    writer.upsert(pg_cur, _nc_payload(seeded_vendor, subtotal=Decimal("250.00")),
                  system_user_id)

    pg_cur.execute("select subtotal from purchase_orders where id=%s", (po_id,))
    assert pg_cur.fetchone()[0] == Decimal("5250.00")


def test_a_po_without_manual_lines_is_untouched_by_the_new_term(
        pg_cur, seeded_vendor, system_user_id):
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)

    pg_cur.execute("select subtotal, total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == (Decimal("100.00"), Decimal("100.00"))


def test_a_full_reload_preserves_a_po_carrying_manual_lines(
        pg_cur, seeded_vendor, system_user_id):
    """Without this the reload deletes the PO, the lines cascade, and a charge
    nobody can re-derive from NC is gone for good."""
    from app.services.nc_purchase_sync import service, writer
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)
    po_id = _add_manual_line(pg_cur, "O1")

    service._full_reload_delete(pg_cur)

    pg_cur.execute("select count(*) from purchase_orders where id=%s", (po_id,))
    assert pg_cur.fetchone()[0] == 1
    pg_cur.execute("select count(*) from po_line_items where po_id=%s and nc_source_pk is null",
                   (po_id,))
    assert pg_cur.fetchone()[0] == 1


def test_a_full_reload_still_clears_an_ordinary_nc_po(pg_cur, seeded_vendor, system_user_id):
    """The preservation must be narrow: a reload that stopped deleting anything
    would quietly stop being a reload."""
    from app.services.nc_purchase_sync import service, writer
    writer.upsert(pg_cur, _nc_payload(seeded_vendor), system_user_id)

    service._full_reload_delete(pg_cur)

    pg_cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] == 0
