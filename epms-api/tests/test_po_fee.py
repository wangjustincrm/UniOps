"""PO Fee Invoices — Incoterms settings, the required Incoterms code on a
raw-material PO, freight / customs invoices linked to its arrivals, and the
per-PO fee status (design: 2026-10-23-po-fee-invoices)."""
import importlib.util
import pathlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.crud import po_fee
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_fee_link import InvoiceFeeLink
from app.models.po import PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"
PO_URL = "/api/v1/po"
CFG_URL = "/api/v1/config"

_MIGRATION = (pathlib.Path(__file__).resolve().parents[1]
              / "alembic" / "versions" / "pf01_po_fee_invoices.py")


def _migration():
    spec = importlib.util.spec_from_file_location("pf01", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _vendor(db, name="Supplier") -> Vendor:
    s = uuid.uuid4().hex[:6]
    v = Vendor(id=uuid.uuid4(), code=f"V{s}", name=f"{name} {s}", category="supplier",
               contact_name="C", contact_email=f"{s}@example.com", payment_terms="net30",
               currency="CAD", is_active=True, is_supplier=True, is_customer=False)
    db.add(v)
    await db.flush()
    return v


async def _po(test_engine, *, code=None, type_=1, source="nc", status="issued",
              notes=None, arrivals=0, place=None):
    """An NC PO with `arrivals` goods receipts. Returns (po_id, [gr_id, ...])."""
    async with _factory(test_engine)() as db:
        creator = (await db.execute(select(User).limit(1))).scalar_one()
        v = await _vendor(db)
        po = PurchaseOrder(
            id=uuid.uuid4(), number=f"PO-FEE-{uuid.uuid4().hex[:8]}", title="NC import",
            type=type_, status=status, currency="CAD", subtotal=Decimal("100"),
            tax_rate=Decimal("0"), tax_amount=Decimal("0"), total=Decimal("100"),
            vendor_id=v.id, vendor_name=v.name, source=source,
            nc_source_pk=uuid.uuid4().hex if source == "nc" else None,
            created_by=creator.id, incoterms_code=code, incoterms=place, notes=notes)
        db.add(po)
        await db.flush()
        grs = []
        for i in range(arrivals):
            g = GoodsReceipt(number=f"DH-{uuid.uuid4().hex[:8]}", title="arrival", po_id=po.id,
                             po_number=po.number, vendor_id=v.id, vendor_name=v.name,
                             gr_type="physical", procurement_type=1, status="collected",
                             source=source, created_by=creator.id,
                             received_at=datetime(2026, 9, 1 + i, tzinfo=timezone.utc))
            db.add(g)
            await db.flush()
            grs.append(g.id)
        await db.commit()
        return po.id, grs


async def _fee_invoice(admin_client, *, lines=1, number=None):
    async with session_module.AsyncSessionLocal() as db:
        v = await _vendor(db, "Forwarder")
        await db.commit()
    r = await admin_client.post(INV_URL, json={
        "vendor_id": str(v.id), "vendor_invoice_number": number or f"FEE-{uuid.uuid4().hex[:8]}",
        "amount": str(100 * lines), "tax_amount": "0", "currency": "CAD",
        "invoice_date": "2026-10-01", "due_date": "2026-10-31",
        "line_items": [{"description": f"Line {i}", "quantity": "1", "unit_price": "100",
                        "line_total": "100"} for i in range(lines)],
    })
    assert r.status_code == 201, r.text
    return r.json()


async def _put_links(client, inv_id, links):
    return await client.put(f"{INV_URL}/{inv_id}/fee-links", json={"links": [
        {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in l.items()} for l in links]})


# ── Incoterms settings ───────────────────────────────────────────────────────

async def test_migration_seed_matches_the_code_defaults():
    seed = [{"code": c, "label": l, "needs_freight": f, "needs_customs": cu, "active": True}
            for c, l, f, cu in _migration()._SEED]
    assert seed == po_fee.DEFAULT_INCOTERMS


async def test_config_serves_the_defaults_and_refuses_removing_a_code(admin_client):
    r = await admin_client.get(CFG_URL)
    assert r.status_code == 200, r.text
    terms = r.json()["incoterms_terms"]
    assert {t["code"] for t in terms} >= {"FOB", "DAP", "DDP"}

    # Deactivating is fine…
    changed = [{**t, "active": t["code"] != "CIF"} for t in terms]
    r = await admin_client.patch(CFG_URL, json={"incoterms_terms": changed})
    assert r.status_code == 200, r.text
    assert next(t for t in r.json()["incoterms_terms"] if t["code"] == "CIF")["active"] is False
    # …removing is not.
    r = await admin_client.patch(CFG_URL, json={"incoterms_terms": changed[1:]})
    assert r.status_code == 422
    assert changed[0]["code"] in r.json()["detail"]
    # Codes are normalised; duplicates refused.
    r = await admin_client.patch(CFG_URL, json={"incoterms_terms": changed + [{"code": "fob"}]})
    assert r.status_code == 422
    # Restore.
    r = await admin_client.patch(CFG_URL, json={"incoterms_terms": po_fee.DEFAULT_INCOTERMS})
    assert r.status_code == 200, r.text


async def test_backfill_splits_code_and_place(test_engine):
    rows = {"DAP CRM": ("DAP", "CRM"), "FOB, Arbuckle, CA": ("FOB", "Arbuckle, CA"),
            "EXW": ("EXW", None), "fca  Toronto": ("FCA", "Toronto"),
            "Delivered to plant": (None, "Delivered to plant"), "DAPX": (None, "DAPX")}
    ids = {}
    for raw in rows:
        pid, _ = await _po(test_engine, place=raw)
        ids[raw] = pid
    mig = _migration()
    async with _factory(test_engine)() as db:
        await db.execute(text(mig.SPLIT_SQL).bindparams(codes=[c for c, *_ in mig._SEED]))
        await db.commit()
        for raw, (code, place) in rows.items():
            po = (await db.execute(select(PurchaseOrder).where(
                PurchaseOrder.id == ids[raw]))).scalar_one()
            assert (po.incoterms_code, po.incoterms) == (code, place), raw


# ── Edit Details: Incoterms required on raw-material orders ──────────────────

async def test_raw_material_po_cannot_be_saved_without_incoterms(admin_client, test_engine):
    pid, _ = await _po(test_engine)
    url = f"{PO_URL}/{pid}/imported-details"
    r = await admin_client.patch(url, json={"buyer_notes": "x"})
    assert r.status_code == 422 and "Incoterms is required" in r.json()["detail"]
    r = await admin_client.patch(url, json={"incoterms_code": "XYZ"})
    assert r.status_code == 422
    r = await admin_client.patch(url, json={"incoterms_code": "fob", "incoterms": "Arbuckle, CA"})
    assert r.status_code == 200, r.text
    assert (r.json()["incoterms_code"], r.json()["incoterms"]) == ("FOB", "Arbuckle, CA")
    # And it cannot be cleared again.
    r = await admin_client.patch(url, json={"incoterms_code": None})
    assert r.status_code == 422


async def test_milk_orders_do_not_require_incoterms(admin_client, test_engine):
    approved, _ = await _po(test_engine, status="nc_milk")
    pending, _ = await _po(test_engine, status="nc_pending", notes="[Milk / 21-Cxx-CRM05]")
    for pid in (approved, pending):
        r = await admin_client.patch(f"{PO_URL}/{pid}/imported-details", json={"buyer_notes": "x"})
        assert r.status_code == 200, r.text


# ── Links and fee status ─────────────────────────────────────────────────────

async def test_freight_per_arrival_and_customs_per_line_complete_the_po(admin_client, test_engine):
    pid, (g1, g2) = await _po(test_engine, code="FOB", arrivals=2)
    other_pid, (g3,) = await _po(test_engine, code="FOB", arrivals=1)

    detail = (await admin_client.get(f"{PO_URL}/{pid}/fee-status")).json()
    assert detail["status"] == "missing"
    assert detail["term"]["needs_freight"] and detail["term"]["needs_customs"]
    assert [a["gr_id"] for a in detail["arrivals"]] == [str(g1), str(g2)]

    # One freight bill for both arrivals (the carrier combined them).
    freight = await _fee_invoice(admin_client)
    r = await _put_links(admin_client, freight["id"], [
        {"fee_type": "freight", "po_id": pid, "gr_id": g1},
        {"fee_type": "freight", "po_id": pid, "gr_id": g2}])
    assert r.status_code == 200, r.text
    assert (await admin_client.get(f"{PO_URL}/{pid}/fee-status")).json()["status"] == "missing"

    # One customs invoice, one line per PO.
    customs = await _fee_invoice(admin_client, lines=3)
    l0, l1, l2 = (li["id"] for li in customs["line_items"])
    r = await _put_links(admin_client, customs["id"], [
        {"fee_type": "customs", "po_id": pid, "gr_id": g1, "invoice_line_id": l0},
        {"fee_type": "customs", "po_id": pid, "gr_id": g2, "invoice_line_id": l1},
        {"fee_type": "customs", "po_id": other_pid, "gr_id": g3, "invoice_line_id": l2}])
    assert r.status_code == 200, r.text

    detail = (await admin_client.get(f"{PO_URL}/{pid}/fee-status")).json()
    assert detail["status"] == "complete"
    a1 = detail["arrivals"][0]
    assert [i["invoice_id"] for i in a1["freight"]] == [freight["id"]]
    assert a1["customs"][0]["line_description"] == "Line 0"
    # Customs alone does not complete the other PO — it has no freight yet.
    assert (await admin_client.get(f"{PO_URL}/{other_pid}/fee-status")).json()["status"] == "missing"

    # The list carries the same roll-up as the detail.
    async with _factory(test_engine)() as db:
        number = (await db.execute(select(PurchaseOrder.number).where(PurchaseOrder.id == pid))).scalar_one()
    items = (await admin_client.get(PO_URL, params={"search": number})).json()["items"]
    assert [(p["id"], p["fee_status"]) for p in items] == [(str(pid), "complete")]


async def test_status_follows_the_incoterms_term(admin_client, test_engine):
    ddp, _ = await _po(test_engine, code="DDP", arrivals=1)
    unset, _ = await _po(test_engine, arrivals=1)
    dap_no_arrival, _ = await _po(test_engine, code="DAP")
    dap, _ = await _po(test_engine, code="DAP", arrivals=1)
    type2, _ = await _po(test_engine, code="FOB", type_=2, source=None, status="issued")
    for pid, want in ((ddp, "not_required"), (unset, "not_set"),
                      (dap_no_arrival, "no_arrivals"), (dap, "missing"), (type2, None)):
        assert (await admin_client.get(f"{PO_URL}/{pid}")).json()["fee_status"] == want, want


async def test_linking_never_touches_matching_or_payment(admin_client, test_engine):
    pid, (g1,) = await _po(test_engine, code="FOB", arrivals=1)
    inv = await _fee_invoice(admin_client)
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/mark-direct")
    assert r.status_code == 200, r.text
    r = await _put_links(admin_client, inv["id"], [{"fee_type": "freight", "po_id": pid, "gr_id": g1}])
    assert r.status_code == 200, r.text
    after = (await admin_client.get(f"{INV_URL}/{inv['id']}")).json()
    assert (after["status"], after["match_route"], after["po_id"]) == ("matched", "direct", None)


async def test_link_validation(admin_client, test_engine):
    pid, (g1,) = await _po(test_engine, code="FOB", arrivals=1)
    other, (g_other,) = await _po(test_engine, code="FOB", arrivals=1)
    no_arrival, _ = await _po(test_engine, code="FOB")
    milk, _ = await _po(test_engine, status="nc_milk")
    epms_po, _ = await _po(test_engine, type_=2, source=None)
    inv = await _fee_invoice(admin_client)

    async def put(*links):
        return await _put_links(admin_client, inv["id"], list(links))

    assert (await put({"fee_type": "freight", "po_id": milk})).status_code == 422
    assert (await put({"fee_type": "freight", "po_id": epms_po})).status_code == 422
    assert (await put({"fee_type": "freight", "po_id": pid, "gr_id": g_other})).status_code == 422
    # A PO with arrivals needs one chosen; one without may be linked as a whole.
    assert (await put({"fee_type": "freight", "po_id": pid})).status_code == 422
    assert (await put({"fee_type": "freight", "po_id": no_arrival})).status_code == 200
    assert (await put({"fee_type": "freight", "po_id": pid, "gr_id": g1,
                       "invoice_line_id": str(uuid.uuid4())})).status_code == 422
    # Duplicates collapse.
    r = await put({"fee_type": "freight", "po_id": pid, "gr_id": g1},
                  {"fee_type": "freight", "po_id": pid, "gr_id": g1})
    assert r.status_code == 200 and len(r.json()) == 1
    got = (await admin_client.get(f"{INV_URL}/{inv['id']}/fee-links")).json()
    assert [(l["po_id"], l["gr_id"]) for l in got] == [(str(pid), str(g1))]


async def test_po_route_invoice_cannot_take_links_but_can_drop_them(admin_client, test_engine):
    pid, (g1,) = await _po(test_engine, code="FOB", arrivals=1)
    inv = await _fee_invoice(admin_client)
    assert (await _put_links(admin_client, inv["id"],
                             [{"fee_type": "freight", "po_id": pid, "gr_id": g1}])).status_code == 200
    async with _factory(test_engine)() as db:
        row = (await db.execute(select(Invoice).where(Invoice.id == uuid.UUID(inv["id"])))).scalar_one()
        row.match_route = "po"
        await db.commit()
    assert (await _put_links(admin_client, inv["id"],
                             [{"fee_type": "freight", "po_id": pid, "gr_id": g1}])).status_code == 422
    r = await _put_links(admin_client, inv["id"], [])
    assert r.status_code == 200 and r.json() == []


async def test_candidates_are_raw_material_pos_with_their_arrivals(admin_client, test_engine):
    pid, (g1,) = await _po(test_engine, code="FOB", arrivals=1)
    milk, _ = await _po(test_engine, status="nc_milk")
    async with _factory(test_engine)() as db:
        number = (await db.execute(select(PurchaseOrder.number).where(PurchaseOrder.id == pid))).scalar_one()
        milk_no = (await db.execute(select(PurchaseOrder.number).where(PurchaseOrder.id == milk))).scalar_one()
        gr_no = (await db.execute(select(GoodsReceipt.number).where(GoodsReceipt.id == g1))).scalar_one()
    for q in (number, gr_no):   # by PO number, and by arrival number
        r = await admin_client.get(f"{INV_URL}/fee-link-candidates", params={"q": q})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert [(p["id"], [a["id"] for a in p["arrivals"]]) for p in body["items"]] == [(str(pid), [str(g1)])]
    r = await admin_client.get(f"{INV_URL}/fee-link-candidates", params={"q": milk_no})
    assert r.json() == {"total": 0, "items": []}


async def test_candidates_put_the_latest_arrival_first_and_report_the_total(admin_client, test_engine):
    # A fee invoice follows a delivery: the order that just received goods
    # must not be pushed off the list by newer orders that have none yet.
    tag = uuid.uuid4().hex[:6]
    arrived, _ = await _po(test_engine, code="FOB", arrivals=1)
    fresh, _ = await _po(test_engine, code="FOB")
    async with _factory(test_engine)() as db:
        for pid, n in ((arrived, "A"), (fresh, "B")):
            po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == pid))).scalar_one()
            po.vendor_name = f"Sort {tag} {n}"
        await db.execute(text("update goods_receipts set received_at = now() where po_id = :p")
                         .bindparams(p=arrived))
        await db.commit()
    r = await admin_client.get(f"{INV_URL}/fee-link-candidates", params={"q": f"Sort {tag}", "limit": 1})
    body = r.json()
    assert body["total"] == 2
    assert [p["id"] for p in body["items"]] == [str(arrived)]


# ── Data protection ──────────────────────────────────────────────────────────

async def test_deleting_an_arrival_keeps_the_link_as_unassigned(admin_client, test_engine):
    pid, (g1,) = await _po(test_engine, code="FOB", arrivals=1)
    inv = await _fee_invoice(admin_client)
    await _put_links(admin_client, inv["id"], [{"fee_type": "freight", "po_id": pid, "gr_id": g1}])
    async with _factory(test_engine)() as db:
        await db.execute(text("delete from goods_receipts where id = :g").bindparams(g=g1))
        await db.commit()
    detail = (await admin_client.get(f"{PO_URL}/{pid}/fee-status")).json()
    assert detail["arrivals"] == []
    assert [i["invoice_id"] for i in detail["unassigned"]] == [inv["id"]]


async def test_full_reload_keeps_pos_with_fee_links_or_incoterms(admin_client, test_engine, test_pg_dsn):
    from app.services.nc_purchase_sync.service import _full_reload_delete
    linked, (g1,) = await _po(test_engine, arrivals=1)
    coded, _ = await _po(test_engine, code="FOB")
    plain, _ = await _po(test_engine)
    inv = await _fee_invoice(admin_client)
    async with _factory(test_engine)() as db:
        db.add(InvoiceFeeLink(invoice_id=uuid.UUID(inv["id"]), fee_type="freight",
                              po_id=linked, gr_id=g1))
        await db.commit()

    import psycopg2
    conn = psycopg2.connect(test_pg_dsn)
    try:
        with conn.cursor() as cur:
            _full_reload_delete(cur)
        conn.commit()
    finally:
        conn.close()

    async with _factory(test_engine)() as db:
        left = set((await db.execute(select(PurchaseOrder.id).where(
            PurchaseOrder.id.in_([linked, coded, plain])))).scalars())
        assert left == {linked, coded}
        assert (await db.execute(select(GoodsReceipt.id).where(GoodsReceipt.id == g1))).first()
