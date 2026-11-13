"""Traceability (user 2026-11-03): the order's lines as one lifecycle
(ordered → shipped → delivered → invoiced) with the pallets and lots under
each line; Lots — every 945 row with its order and customer, searchable,
groupable, "Expiring soon", and a recall by one or several lots (CSV too); the
packing list per shipment; the 945's N9*E1 kept; and nothing ever deleted."""
import csv
import io
from datetime import date
from decimal import Decimal


from app.domain.lifecycle import line_state
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.query import QuerySpec, parse_filter, parse_sort
from app.services.lots import LotService
from app.services.order_views import OrderService
from tests.test_invoicing import OP, confirm, p6  # noqa: F401 — p6 is a fixture
from tests.test_order_ref import pdf_text
from tests.test_orders import env, po, run_850  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import store_945, x945

HEADER = ["N1*ST*Loblaws DC 41", "N4*Ajax*ON*L1Z 1X3*CA", "DTM*011*20261102", "N9*BM*BOL-778", "N9*E1*SO-00042", "N9*2I*1Z-TRACK"]
TWO_PALLETS = ["LX*1", "MAN*GM*00077777770077782891", "W12*CP*60*40*0*EA**VN*CF0092", "N9*LT*LOT-A", "DTM*036*20280519",
               "LX*2", "MAN*GM*00077777770077782907", "W12*CP*60*20*0*EA**VN*CF0092", "N9*LT*LOT-B", "DTM*036*20261120"]


async def shipped(t, number="PO-1", partner="LBL", qty=10, rows=TWO_PALLETS):
    await run_850(t.edi.env, {"PurchaseOrder": [po(partner, number, [
        {"Line": 1, "BuyerPart": {"LBL": "L-555", "SOB": "S-777"}[partner], "Quantity": qty, "UOM": "CA", "UnitPrice": "50"}],
        ship_to_gln="0012345000011")]})
    o, _ = await t.edi.env.order(number)
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = F.ACCEPTED_940.value
        await u.commit()
    await store_945(t, x945(f"{o.order_number:09d}", rows, header=HEADER, ctl=f"{900000000 + o.id}"), f"945-{number}.edi")
    await t.ships.process_945s()
    return o


async def lots(t, **kw):
    async with t.factory(OP) as u:
        return await LotService(u).list(QuerySpec(**kw))


def test_a_line_is_where_its_furthest_step_got_it():
    st = lambda **kw: line_state(**{"removed": False, "qty_ea": 60, "shipped_ea": None, "delivered_ea": None, "invoiced": False} | kw)  # noqa: E731
    assert st() == "Open"
    assert (st(shipped_ea=60), st(shipped_ea=50), st(shipped_ea=70)) == ("Shipped", "Short shipped", "Over shipped")
    assert (st(shipped_ea=60, delivered_ea=60), st(shipped_ea=60, delivered_ea=54)) == ("Delivered", "Short delivered")
    assert st(shipped_ea=60, delivered_ea=60, invoiced=True) == "Invoiced"
    assert st(removed=True, invoiced=True) == "Removed"


async def test_the_detail_gives_each_line_its_lifecycle_and_the_lots_packed_for_it(p6):  # noqa: F811
    o = await shipped(p6)
    async with p6.factory(OP) as u:
        d = await OrderService(u).detail(o.id)
    assert (d.total, d.total_amount) == (None, Decimal("500.00"))  # an 850 has no total: its lines at the PO price
    [ln] = d.lines
    assert (ln.qty_ea, ln.shipped_ea, ln.state, ln.invoiced_qty) == (60, 60, "Shipped", None)
    assert [(p.sscc, p.lot, p.expiry, p.qty_shipped_ea, p.tracking, p.ship_date) for p in ln.packing] == [
        ("00077777770077782891", "LOT-A", date(2028, 5, 19), 40, "1Z-TRACK", date(2026, 11, 2)),
        ("00077777770077782907", "LOT-B", date(2026, 11, 20), 20, "1Z-TRACK", date(2026, 11, 2))]
    assert d.shipments[0].our_reference == "SO-00042"  # the 945's N9*E1, kept
    await p6.inv.advance()
    await confirm(p6, o.id)
    async with p6.factory(OP) as u:
        [ln] = (await OrderService(u).detail(o.id)).lines
    assert (ln.delivered_qty, ln.invoiced_qty, ln.invoice_unit, str(ln.invoice_price), str(ln.invoice_amount), ln.state) == (
        60, 10, "CA", "50.00000", "500.00", "Invoiced")


async def test_lots_give_every_order_and_customer_a_lot_went_to(p6):  # noqa: F811
    a = await shipped(p6, "PO-1")
    b = await shipped(p6, "PO-2", rows=["LX*1", "W12*CP*60*60*0*EA**VN*CF0092", "N9*LT*LOT-A", "DTM*036*20280519"])
    p = await lots(p6, sort=parse_sort("order_ref,lot"))
    assert [(r.lot, r.order_ref, r.partner_name, r.ship_to_city, r.ship_to_postal, r.qty_shipped_ea, r.our_reference) for r in p.items] == [
        ("LOT-A", "LBL-PO-1", "Loblaws", "Ajax", "L1Z 1X3", 40, "SO-00042"), ("LOT-B", "LBL-PO-1", "Loblaws", "Ajax", "L1Z 1X3", 20, "SO-00042"),
        ("LOT-A", "LBL-PO-2", "Loblaws", "Ajax", "L1Z 1X3", 60, "SO-00042")]
    assert [(t.currency, t.sums) for t in p.totals] == [(None, {"qty_shipped_ea": "120"})]
    # a recall: one lot, or several
    recall = await lots(p6, filters=[parse_filter("lot:in:LOT-A")])
    assert sorted(r.order_id for r in recall.items) == [a.id, b.id] and recall.totals[0].sums["qty_shipped_ea"] == "100"
    assert (await lots(p6, filters=[parse_filter("lot:in:LOT-A|LOT-B")])).total == 3
    assert [r.lot for r in (await lots(p6, search="lot-b")).items] == ["LOT-B"]
    by_lot = await lots(p6, group=parse_sort("lot"))
    assert [(g.key, g.count, g.totals[0].sums["qty_shipped_ea"]) for g in by_lot.groups] == [(["LOT-A"], 2, "100"), (["LOT-B"], 1, "20")]
    async with p6.factory(OP) as u:
        data = await LotService(u).export_csv(QuerySpec(filters=[parse_filter("lot:in:LOT-A")], sort=parse_sort("order_ref")))
    rows = list(csv.DictReader(io.StringIO(data.decode())))
    assert [(r["lot"], r["order_ref"], r["partner_name"], r["ship_to_postal"], r["tracking"]) for r in rows] == [
        ("LOT-A", "LBL-PO-1", "Loblaws", "L1Z 1X3", "1Z-TRACK"), ("LOT-A", "LBL-PO-2", "Loblaws", "L1Z 1X3", "1Z-TRACK")]


async def test_expiring_soon_is_the_next_30_days(p6):  # noqa: F811
    await shipped(p6)
    async with p6.factory(OP) as u:
        meta = await LotService(u).meta()
        [view] = [v for v in meta["views"] if v["key"] == "expiring_soon"]
    [days] = await p6.q("SELECT ('2026-11-20'::date - current_date) d")
    soon = await lots(p6, filters=[parse_filter("days_to_expiry:between:0..30")])
    assert [r.lot for r in soon.items] == (["LOT-B"] if 0 <= days["d"] <= 30 else [])
    assert view["state"]["filters"] == [{"field": "days_to_expiry", "op": "between", "value": [0, 30]}]


async def test_the_packing_list_has_pallets_lots_expiry_tracking_and_bol(p6):  # noqa: F811
    o = await shipped(p6)
    [sh] = await p6.q("SELECT id FROM sales_shipments")
    async with p6.factory(OP) as u:
        name, pdf = await OrderService(u).packing_list(o.id, sh["id"])
    assert name == f"PackingList-LBL-PO-1-{sh['id']}.pdf"
    body = pdf_text(pdf)
    for s in (b"PACKING LIST", f"PL-{o.order_number}".encode(), b"LBL-PO-1", b"BOL-778", b"1Z-TRACK", b"00077777770077782891",
              b"LOT-A", b"19-May-2028", b"LOT-B", b"Shipment total", b"SO-00042", b"Ajax", b"Page 1 of 1"):
        assert s in body, s
    async with p6.factory(OP) as u:  # another order's shipment is not this order's
        other = await shipped(p6, "PO-2")
        try:
            await OrderService(u).packing_list(other.id, sh["id"])
            raise AssertionError("another order's shipment was printed")
        except Exception as e:  # noqa: BLE001
            assert type(e).__name__ == "NotFound"


async def test_nothing_is_deleted_a_second_945_keeps_the_first_and_its_document(p6):  # noqa: F811
    o = await shipped(p6)
    before = await p6.q("SELECT id, sscc, lot FROM sales_shipment_rows ORDER BY id")
    # a second, different 945 for the same order
    await store_945(p6, x945(f"{o.order_number:09d}", ["LX*1", "W12*CP*60*60*0*EA**VN*CF0092", "N9*LT*LOT-C"],
                             header=HEADER, ctl="900000999"), "945-again.edi")
    await p6.ships.process_945s()
    after = await p6.q("SELECT id, sscc, lot FROM sales_shipment_rows ORDER BY id")
    assert after[:len(before)] == before and len(after) == len(before) + 1  # kept, one more
    docs = await p6.q("SELECT count(*) n FROM sales_documents WHERE kind = '945_EDI' AND content IS NOT NULL")
    assert docs == [{"n": 2}]  # both raw 945s, as received
    p = await lots(p6, filters=[parse_filter("lot:eq:LOT-C")])
    assert [(r.applied, r.order_ref) for r in p.items] == [(False, "LBL-PO-1")]  # listed, marked not applied
