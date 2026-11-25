"""A command that changes only a record's parts (its lines, its units) moves the record's version (V1, 2026-11-25; platform/parts.py):
the second of two people who saw the same is refused (409), never written over — and the first goes on with the version their save
answered. A record just made is version 1, whatever its parts."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.shipments import Shipment, ShipmentRow
from app.platform.errors import BusinessError
from app.schemas.invoices import InvoiceEdit
from app.services.invoice_views import InvoiceService
from app.services.order_holds import OrderActions
from tests.conftest import make_token
from tests.test_invoicing import OP, confirm, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_lock_order_shopify import p5, to_decide  # noqa: F401 — p5 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_orders_api import GRANTS, api  # noqa: F401 — api is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

TO = {"name": "A B", "phone": "514-555-0100", "address1": "1 rue", "city": "Montréal", "province": "QC", "zip": "h2x1y4"}


CODES = {3: "CF0092", 5: "CRMMARK002", 7: "CF0010"}


class Http:
    def __init__(self, api) -> None:  # noqa: F811
        GRANTS["parts"] = {"sales.read", "sales.order.operate", "sales.config", "sales.transfer.manage", "sales.inventory.adjust", "sales.invoice.manage"}
        self.c, self.h = api, {"Authorization": f"Bearer {make_token('parts')}"}

    async def __call__(self, m, u, **k):
        return await self.c.request(m, "/api/v1" + u, headers=self.h, **k)

    async def made(self, u, body):
        r = await self("POST", u, json=body)
        assert r.status_code == 201, r.text
        return r.json()


async def manual(h):
    body = lambda n: {"partner_code": "MAN", "ship_to": TO, "lines": [{"item_code": "CF0092", "quantity": n, "unit": "CASE"}]}  # noqa: E731
    o = await h.made("/orders/manual", body(2))
    return f"/orders/manual/{o['id']}", f"/orders/{o['id']}", body, lambda d: d["lines"][0]["qty_ea"] // 6


async def transfer(h):
    a, b = [(await h.made("/config/warehouses", {"code": c, "name": c, "role": r}))["id"] for c, r in (("WA1", "SHIPPER"), ("WB1", "WAREHOUSE"))]
    body = lambda n: {"from_warehouse_id": a, "to_warehouse_id": b, "lines": [{"item_code": "CF0092", "qty_ea": n}]}  # noqa: E731
    t = await h.made("/transfers", body(2))
    return f"/transfers/{t['id']}", f"/transfers/{t['id']}", body, lambda d: d["lines"][0]["qty_ea"]


async def units(h):
    i = await h.made("/config/items", {"item_code": "CFZ1", "warehouse_held": True, "units": [{"unit": "EA", "factor_ea": "1"}]})
    body = lambda n: {"units": [{"unit": "EA", "factor_ea": "1"}, {"unit": "CASE", "factor_ea": str(n)}]}  # noqa: E731
    return f"/config/items/{i['id']}", f"/config/items/{i['id']}", body, lambda d: next(int(float(u["factor_ea"])) for u in d["units"] if u["unit"] == "CASE")


async def unit_titles(h):
    i = await h.made("/config/items", {"item_code": "CFZ2", "warehouse_held": True, "units": [{"unit": "EA", "factor_ea": "1"}]})
    body = lambda n: {"units": [{"unit": "EA", "factor_ea": "1", "shopify_titles": [f"T{n}"]}]}  # noqa: E731
    return f"/config/items/{i['id']}", f"/config/items/{i['id']}", body, lambda d: int(d["units"][0]["shopify_titles"][0][1:])


async def unit_taken_off(h):
    start = [{"unit": "EA", "factor_ea": "1"}, {"unit": "CASE", "factor_ea": "6"}]
    i = await h.made("/config/items", {"item_code": "CFZ3", "warehouse_held": True, "units": start})
    body = lambda n: {"units": [start[0], *([start[1]] if n != 3 else []), *([{"unit": "2PACK", "factor_ea": str(n)}] if n != 3 else [])]}  # noqa: E731
    return f"/config/items/{i['id']}", f"/config/items/{i['id']}", body, lambda d: next((int(float(u["factor_ea"])) for u in d["units"] if u["unit"] == "2PACK"), 3)


async def partner(h):
    p = await h.made("/config/partners", {"code": "PV1", "name": "PV", "abbrev": "PV1", "channel": "MANUAL", "settlement_mode": "PREPAID",
                                          "sending_mode": "AUTOMATIC", "delay_minutes": 0, "invoice_method": "NONE", "dc_routing_required": False})
    body = lambda n: {"invoice_cc": f"a{n}@x.test"}  # noqa: E731
    return f"/config/partners/{p['id']}", f"/config/partners/{p['id']}", body, lambda d: int(d["invoice_cc"][1])


async def partner_carriers(h):
    u, r, _, _ = await partner(h)
    body = lambda n: {"carrier_defaults": {"x": f"c{n}"}}  # noqa: E731
    return u, r, body, lambda d: int(d["carrier_defaults"]["x"][1:])


async def partner_code(h):
    c = await h.made("/config/partner-codes", {"partner_code": "SOB", "buyer_part": "PV-1", "item_code": "CF0010"})
    return f"/config/partner-codes/{c['id']}", f"/config/partner-codes/{c['id']}", lambda n: {"item_code": CODES[n]}, lambda d: next(k for k, v in CODES.items() if v == d["item_code"])


async def dc(h):
    c = await h.made("/config/dc-routing", {"partner_code": "SOB", "ship_to_gln": "0062639300019", "dc_number": "1"})
    return f"/config/dc-routing/{c['id']}", f"/config/dc-routing/{c['id']}", lambda n: {"dc_number": str(n)}, lambda d: int(d["dc_number"])


async def warehouse(h):
    w = await h.made("/config/warehouses", {"code": "WV1", "name": "W", "role": "SHIPPER"})
    return f"/config/warehouses/{w['id']}", f"/config/warehouses/{w['id']}", lambda n: {"address_lines": f"line {n}"}, lambda d: int(d["address_lines"][5:])


CASES = [manual, transfer, units, unit_titles, unit_taken_off, partner, partner_carriers, partner_code, dc, warehouse]


@pytest.mark.parametrize("make", CASES, ids=lambda f: f.__name__)
async def test_the_second_save_with_the_old_version_is_refused_and_the_first_goes_on(api, make):  # noqa: F811
    h = Http(api)
    url, read, body, value = await make(h)
    seen = (await h("GET", read)).json()["version"]
    assert seen == (2 if make is manual else 1)  # a manual order's own reference is set once its number is known
    first = await h("PUT", url, params={"version": seen}, json=body(3))
    assert first.status_code == 200, first.text
    moved = first.json()["version"]
    assert moved > seen and (await h("GET", read)).json()["version"] == moved  # what the save answers is what the next one sends
    second = await h("PUT", url, params={"version": seen}, json=body(5))
    assert second.status_code == 409 and second.json()["error"]["code"] == "entity.stale", second.text
    d = (await h("GET", read)).json()
    assert (value(d), d["version"]) == (3, moved)  # the first one's, kept
    again = await h("PUT", url, params={"version": moved}, json=body(7))
    assert again.status_code == 200 and value((await h("GET", read)).json()) == 7


async def test_the_same_units_again_do_not_move_the_item(api):  # noqa: F811
    h = Http(api)
    url, read, body, _ = await units(h)
    v = (await h("PUT", url, params={"version": (await h("GET", read)).json()["version"]}, json=body(6))).json()["version"]
    r = await h("PUT", url, params={"version": v}, json=body(6))
    assert r.status_code == 200 and r.json()["version"] == v
    off = {"units": [{"unit": "EA", "factor_ea": "1"}]}  # CASE taken off (kept, not active): the same again does not move it either
    v = (await h("PUT", url, params={"version": v}, json=off)).json()["version"]
    r = await h("PUT", url, params={"version": v}, json=off)
    assert r.status_code == 200 and r.json()["version"] == v
    assert (await h("PUT", url, params={"version": v}, json=body(6))).json()["version"] > v  # back again: a change


@pytest.mark.parametrize("action", ["submit_draft", "cancel_draft"])
async def test_a_draft_edited_meanwhile_is_not_submitted_nor_cancelled_by_who_saw_it_before(api, action):  # noqa: F811
    h = Http(api)
    url, read, body, _ = await manual(h)
    seen = (await h("GET", read)).json()["version"]
    moved = (await h("PUT", url, params={"version": seen}, json=body(3) | {"carrier": "LTL", "reason": "Sample"})).json()["version"]
    moved = (await h("PUT", url, params={"version": moved}, json=body(4) | {"carrier": "LTL", "reason": "Sample"})).json()["version"]  # the lines only
    r = await h("POST", f"/orders/actions/{action}", json={"ids": {read.split('/')[-1]: moved - 1}})
    assert r.status_code == 200, r.text
    [out] = r.json()["results"]
    assert (out["status"], out["code"]) == ("failed", "entity.stale") or (out["status"], out["code"]) == ("skipped", "entity.stale"), out
    d = (await h("GET", read)).json()
    assert (d["fulfillment_status"], d["version"]) == ("Draft", moved)
    r = await h("POST", f"/orders/actions/{action}", json={"ids": {read.split('/')[-1]: moved}})
    assert r.json()["results"][0]["status"] == "done", r.text


async def test_a_count_s_note_and_moment(api):  # noqa: F811
    h = Http(api)
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    from tests.test_stock_counts import SUMMARY
    r = await h("POST", "/inventory/counts", files={"file": ("i.csv", SUMMARY.encode(), "text/csv")}, data={"counted_at": when.isoformat()})
    assert r.status_code == 201, r.text
    c = r.json()
    put = lambda v, note: h("PUT", f"/inventory/counts/{c['id']}", json={"version": v, "note": note})  # noqa: E731
    one = await put(c["version"], "first")
    assert one.status_code == 200 and one.json()["version"] > c["version"]
    two = await put(c["version"], "second")
    assert (two.status_code, two.json()["error"]["code"]) == (409, "entity.stale")
    assert (await h("GET", f"/inventory/counts/{c['id']}")).json()["note"] == "first"
    assert (await put(one.json()["version"], "third")).status_code == 200


async def test_an_invoice_s_lines(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-PV1")
    await p6.inv.advance()
    inv = await confirm(p6, o.id)

    async def edit(v, price):
        async with p6.factory(OP) as u:
            out = await InvoiceService(u).edit(p6.inv, inv.id, InvoiceEdit(reason="agreed", lines=[
                {"id": inv.lines[0].id, "qty": 10 if price == 45 else 9, "unit_price": str(price)}]), v)
            await u.commit()
            return out
    one = await edit(inv.version, 45)
    assert one.version > inv.version
    with pytest.raises(BusinessError) as e:
        await edit(inv.version, 46)
    assert e.value.code == "entity.stale"
    two = await edit(one.version, 50)  # 9 × 50 = 10 × 45: the total stands, only the line changes
    assert two.version > one.version
    with pytest.raises(BusinessError) as e:
        await edit(one.version, 47)
    assert e.value.code == "entity.stale"


async def test_a_945_row_linked_to_a_line_moves_the_order_and_accepting_one_does_not(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)
    version = lambda: p5.q("SELECT version FROM sales_orders WHERE id = :i", i=oid)  # noqa: E731
    [line] = await p5.q("SELECT id, shipped_ea FROM sales_order_lines WHERE order_id = :o", o=oid)
    async with p5.factory(OP) as u:  # the row is of the line's item now: it can be linked
        await u.session.get(Shipment, sid, with_for_update=True)
        (await u.session.get(ShipmentRow, rid)).item_code = "CF0092"
        await u.commit()
    [seen] = await version()

    async def decide(line_id, why):
        async with p5.factory(OP) as u:
            o = await OrderActions(u, p5.edi.env.tasks).resolve_row(sid, rid, line_id, why)
            await u.commit()
            return o.version
    answered = await decide(line["id"], None)
    [now] = await version()
    assert now["version"] > seen["version"] and answered == now["version"]
    [after] = await p5.q("SELECT shipped_ea FROM sales_order_lines WHERE id = :i", i=line["id"])
    assert after["shipped_ea"] > (line["shipped_ea"] or 0)
    with pytest.raises(BusinessError) as e:  # it takes no version: a second decision is refused by the row
        await decide(line["id"], None)
    assert e.value.code == "shipment.row_resolved"
