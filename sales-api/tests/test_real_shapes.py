"""The real files (2026-11-03: Radiant's 945s and 997s, TrueCommerce's 850s
on dev2), anonymised into tests/fixtures/*_real_shape.* — names, addresses,
tracking, bills and lots replaced; the segment structure and codes kept — and
the fixes they led to: G62 expiry and ship date, per-parcel tracking, the
PO / Zoho reference in the 945 header, group-level 997s, the 850's Item list
and nested parties, an order with no lines, same-item lines sharing what was
shipped, and the shadow run's PO fallback and quieter checks."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio

from app.domain.edi945 import parse_945
from app.domain.edi997 import parse_997
from app.domain.sources import from_850
from app.domain.status import Fulfillment as F
from app.platform.actors import SYSTEM_ACTOR
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.shipments import Shipments
from tests.test_orders import env, run_850  # noqa: F401 — env is a fixture
from tests.test_outbound import a_paid_order, docs, sent_order, status, store_997
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import store_945, x945

FIX = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def edi(p4_edi):  # noqa: F811 — the P4 fixture (writes on), under the name these tests use
    return p4_edi


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


# ── parsers ───────────────────────────────────────────────────────────────────
def test_a_real_shopify_945_g62_dates_lots_parcel_tracking_po_and_zoho_reference():
    [sh] = parse_945(fixture("radiant_945_real_shape.edi"))
    assert (sh.order_key_raw, sh.po, sh.shipment_id, sh.reference) == ("9301", "9301", "1790001", "SHP-9301")
    assert (sh.ship_date, sh.ship_date_source, sh.mode, sh.carrier) == (date(2026, 11, 2), "G62", "U", "PRLA")
    assert (sh.carrier_ref, sh.bol) == ("1000001", "5001")
    assert [(r.item_code, r.qty_ordered, r.qty_shipped, r.uom, r.upc, r.status) for r in sh.rows] == [
        ("CF0092", 1, 1, "EA", "628347940140", "CC"), ("CF0102", 4, 4, "EA", "628347940355", "CC"),
        ("CRMMARK002", 1, 1, "EA", None, "CC")]
    assert [(r.lot, r.expiry) for r in sh.rows] == [
        ("LOT-SAMPLE-A", date(2028, 9, 8)), ("LOT-SAMPLE-B", date(2028, 9, 2)), (None, None)]  # 19000101 = none
    assert [r.tracking for r in sh.rows] == ["TRK000000001", "TRK000000002", "TRK000000002"]  # per parcel
    assert sh.tracking == ["TRK000000001", "TRK000000002"]  # the shipment's, for the Shopify write-back
    assert {r.sscc for r in sh.rows} == {"00077777770000000017"} and {r.lx_index for r in sh.rows} == {1}  # one pallet
    st = sh.parties["ST"]
    assert (st.province, st.zip, st.country) == ("QC", "H2X1Y4", "CA")


def test_a_real_retail_945_two_pallets_ltl_and_the_po_in_w06_06():
    [sh] = parse_945(fixture("radiant_945_retail_real_shape.edi"))
    assert (sh.order_key_raw, sh.po, sh.reference) == ("001008597", "4630564306", "CRM-940_001008597_Sobeys_4630564306")
    assert (sh.mode, sh.carrier, sh.tracking) == ("LT", "WIFS", [])
    assert [(r.lx_index, r.sscc, r.item_code, r.qty_shipped, r.lot, r.expiry) for r in sh.rows] == [
        (1, "00077777770000000024", "CF0092", 300, "LOT-SAMPLE-A", date(2028, 9, 8)),
        (2, "00077777770000000031", "CF0093", 180, "LOT-SAMPLE-C", date(2028, 4, 16))]


def test_a_real_997_is_group_level_only():
    [g] = parse_997(fixture("radiant_997_real_shape.edi"))
    assert (g.functional_id, g.group_control, g.code, g.sets, g.group_only, g.accepted) == ("OW", "1008646", "A", [], True, True)


def test_a_shopify_contact_is_the_orders_phone_else_the_ship_tos_and_the_customer_id():
    from app.domain.sources import from_shopify
    base = {"id": 1, "name": "#1", "email": "a@b.example", "customer": {"id": 77, "phone": "3"},
            "shipping_address": {"name": "A", "phone": "2"}, "line_items": []}
    get = lambda o: from_shopify(o, partner_code="S", partner_abbrev="SHP", cutoff=None).header["contact"]  # noqa: E731
    assert get(base | {"phone": "1"}) == {"email": "a@b.example", "phone": "1", "customer_id": "77"}
    assert get(base)["phone"] == "2"


def test_a_real_850_item_list_nested_ship_to_and_bill_to_case_and_dates():
    [src] = from_850(json.loads(fixture("truecommerce_850_real_shape.json")))
    assert (src.partner_code, src.source_order_id) == ("LBL", "4800000001")
    h = src.header
    assert (h["order_date"], h["requested_delivery_date"]) == (date(2026, 11, 3), date(2026, 11, 10))
    assert h["ship_to"] == {"gln": "0000000000028", "name": "SAMPLE DISTRIBUTION CENTRE", "address1": "3 SAMPLE AVENUE",
                            "address2": None, "city": "BRAMPTON", "province": "ON", "zip": "L6T 1A1", "country": "CA"}
    assert (h["bill_to"]["name"], h["bill_to"]["code"], h["bill_to"]["gln"]) == ("SAMPLE BUYER", "0000000000099", "0000000000099")
    assert h["bill_to"]["address1"] == "4 SAMPLE STREET"
    # Walmart's 850s give only a code and a name: the ship-to's address is carried, as in Zoho's 940 and 810
    po = json.loads(fixture("truecommerce_850_real_shape.json"))["PurchaseOrder"][0]
    po["BillTo"] = {"BillToCode": "0000000000099", "BillToName": "SAMPLE BUYER"}
    bt = from_850({"PurchaseOrder": [po]})[0].header["bill_to"]
    assert (bt["name"], bt["address1"], bt["zip"]) == ("SAMPLE BUYER", h["ship_to"]["address1"], h["ship_to"]["zip"])
    assert [(ln.line_no, ln.quantity, ln.unit, ln.inner_packs, ln.identifiers["upc"], ln.identifiers["description"])
            for ln in src.lines] == [(10, 200, "CASE", 6, "628347940140", "SAMPLE ITEM ONE"),
                                     (20, 12, "CASE", 12, "062639300029", "SAMPLE ITEM TWO")]


# ── orders from the real 850, and one with no lines ──────────────────────────
async def test_the_real_850_becomes_an_order_with_its_lines_and_one_without_lines_goes_to_review(env):  # noqa: F811
    out = await run_850(env, json.loads(fixture("truecommerce_850_real_shape.json")))
    assert out["doc"]["processing_status"] == "Processed"
    o, lines = await env.order("4800000001")
    assert o.fulfillment_status == F.PENDING and o.ship_to["zip"] == "L6T 1A1"
    assert [(ln.item_code, ln.order_unit, ln.qty_ea) for ln in lines] == [("CF0092", "CASE", 1200), ("CF0010", "CASE", 144)]
    empty = json.loads(fixture("truecommerce_850_real_shape.json"))
    empty["PurchaseOrder"][0] |= {"PurchaseOrderNumber": "4800000002", "Item": []}
    await run_850(env, empty)
    e, no_lines = await env.order("4800000002")
    assert no_lines == [] and e.fulfillment_status == F.REVIEW and "no lines" in e.review_reason


# ── 997s at the group level, live ─────────────────────────────────────────────
async def test_a_group_level_997_accepts_or_rejects_the_940_with_that_gs06(edi):
    o, d = await sent_order(edi)
    [row] = await edi.env.q("SELECT gs06 FROM sales_documents WHERE id = :i", i=d["id"])
    group = ("ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1300*U*00401*000017092*0*P*>~\n"
             "GS*FA*9055651212*CANADAROYALMILK*20261102*1300*17092*X*004010~\nST*997*17092~\n"
             f"AK1*OW*{int(row['gs06']):09d}~\nAK9*A*1*1*1*6~\nSE*4*17092~\nGE*1*17092~\nIEA*1*000017092~\n").encode()
    await store_997(edi, group, "997-group.edi")
    r = await edi.out.process_997s()
    assert r.acks == 1 and await status(edi, o.id) == F.ACCEPTED_940
    [d2] = [x for x in await docs(edi) if x["id"] == d["id"]]
    assert d2["ack_status"] == "Accepted"
    o2, d3 = await sent_order(edi, number=9302)
    [row2] = await edi.env.q("SELECT gs06 FROM sales_documents WHERE id = :i", i=d3["id"])
    rejected = group.replace(f"AK1*OW*{int(row['gs06']):09d}".encode(), f"AK1*OW*{int(row2['gs06']):09d}".encode()) \
        .replace(b"AK9*A*", b"AK9*R*")
    await store_997(edi, rejected, "997-group-r.edi")
    await edi.out.process_997s()
    assert await status(edi, o2.id) == F.REJECTED_940
    [t] = await edi.env.q("SELECT description FROM tasks WHERE type = 'sales_940_rejected'")
    assert "AK9 R (group level, no AK2)" in t["description"]


# ── the 945 plan: same-item lines, the shadow run ─────────────────────────────
@pytest_asyncio.fixture
async def p5(edi):
    ships = Shipments(uow_factory=edi.env.factory, tasks=edi.env.tasks, shopify=edi.gateways.shopify, outbound=edi.out,
                      clock=lambda: edi.clock.now)
    return SimpleNamespace(edi=edi, ships=ships, q=edi.env.q, factory=edi.env.factory)


async def two_lines_of_one_item(t, number=9488):
    """Orders 9488 / 9514's shape: a "Trial Can" and a "Can", both CF0092, 1 EA each."""
    o = await a_paid_order(t.edi, number=number, lines=[
        {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Trial Can", "quantity": 1, "price": "1",
         "requires_shipping": True},
        {"id": 12, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Can", "quantity": 1, "price": "30",
         "requires_shipping": True}])
    return o


async def test_one_w12_for_two_lines_of_the_same_item_fills_both_not_an_over_shipment(p5):
    o = await two_lines_of_one_item(p5)
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9488", ["LX*1", "W12*CC*2*2*0*EA**VN*CF0092"]))
    await p5.ships.process_945s()
    lines = await p5.q("SELECT line_no, qty_ea, shipped_ea FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=o.id)
    assert [(ln["qty_ea"], ln["shipped_ea"]) for ln in lines] == [(1, 1), (1, 1)]
    [sh] = await p5.q("SELECT checks FROM sales_shipments")
    assert not any(c["check"] == "over-shipment" for c in sh["checks"] or [])
    assert await status(p5.edi, o.id) == F.SHIPPED


async def test_more_than_the_same_item_lines_ordered_is_still_an_over_shipment(p5):
    o = await two_lines_of_one_item(p5, number=9514)
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9514", ["LX*1", "W12*CC*2*3*0*EA**VN*CF0092"]))
    await p5.ships.process_945s()
    lines = await p5.q("SELECT shipped_ea FROM sales_order_lines WHERE order_id = :o ORDER BY line_no", o=o.id)
    assert [ln["shipped_ea"] for ln in lines] == [2, 1]  # the extra one on the first line
    [sh] = await p5.q("SELECT checks FROM sales_shipments")
    [over] = [c for c in sh["checks"] if c["check"] == "over-shipment"]
    assert (over["found"], over["expected"]) == (2, 1)


async def shadow_on(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()


async def test_in_the_shadow_run_no_sales_940_is_no_check_and_the_po_matches_zohos_number(p5):
    await run_850(p5.edi.env, json.loads(fixture("truecommerce_850_real_shape.json")))  # PO 4800000001
    await shadow_on(p5)
    o = await a_paid_order(p5.edi, number=9301)
    await store_945(p5, x945("9301", ["LX*1", "W12*CC*12*12*0*EA**VN*CF0092"]), "a.edi", shadow=True)
    # Zoho's own number in W06-02, the PO in W06-06 (the real Sobeys 945's shape)
    await store_945(p5, x945("001008597", ["LX*1", "W12*CC*1200*1200*0*EA*628347940140*VN*CF0092"],
                             header=["N1*ST*DC", "G62*11*20261102", "N9*PO*4800000001"]), "b.edi", shadow=True)
    # a Zoho manual order: no PO anywhere
    await store_945(p5, x945("C0031", ["LX*1", "W12*CC*1*1*0*EA**VN*CF0092"], header=["N1*ST*X", "G62*11*20261102"]),
                    "c.edi", shadow=True)
    await p5.ships.process_945s()
    rows = {r["remote_name"]: r for r in await p5.q(
        "SELECT d.remote_name, s.order_id, s.checks FROM sales_shipments s JOIN sales_documents d ON d.id = s.document_id")}
    assert rows["a.edi"]["order_id"] == o.id
    assert not any(c["message"] == "no 940 was sent by Sales for this order" for c in rows["a.edi"]["checks"] or [])
    [po_order] = await p5.q("SELECT id FROM sales_orders WHERE source_order_number = '4800000001'")
    assert rows["b.edi"]["order_id"] == po_order["id"]  # matched by its PO in the shadow run
    assert rows["c.edi"]["order_id"] is None             # needs Zoho's reference 940
    assert await status(p5.edi, o.id) == F.PENDING       # the shadow run applies nothing


async def test_outside_the_shadow_run_zohos_number_is_not_matched_by_po(p5):
    await run_850(p5.edi.env, json.loads(fixture("truecommerce_850_real_shape.json")))
    await store_945(p5, x945("001008597", ["LX*1", "W12*CC*1200*1200*0*EA*628347940140*VN*CF0092"],
                             header=["N1*ST*DC", "G62*11*20261102", "N9*PO*4800000001"]), "b.edi")
    await p5.ships.process_945s()
    [sh] = await p5.q("SELECT order_id FROM sales_shipments")
    assert sh["order_id"] is None  # FR-OB-28's own keys only: a live 945 carries Sales' number


def test_only_the_customers_id_email_and_phone_are_kept():
    from app.integrations.shopify import ORDER_FIELDS, slim_customer
    assert "customer" in ORDER_FIELDS  # the 940's N1 id: a fetch without it would send every N1 without an id
    o = slim_customer({"id": 1, "customer": {"id": 77, "email": "a@b.example", "phone": "1", "first_name": "A",
                                             "default_address": {"address1": "x"}}})
    assert o["customer"] == {"id": 77, "email": "a@b.example", "phone": "1"}
    assert slim_customer({"id": 1, "customer": None}) == {"id": 1, "customer": None}
