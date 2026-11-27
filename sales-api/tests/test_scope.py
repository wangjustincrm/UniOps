"""A page's scope (user 2026-11-27): filters the person cannot remove there — an item's Orders, Invoices, Transfers,
Shipped. The server ANDs it into everything the list does: its rows, its count, its export and its selection's actions
(chosen ids outside it are refused, nothing done). "Has a line with item X" on Orders, Invoices and Transfers; Shipped,
the applied 945 rows with their customer (a Shopify one as an initial and the city) and the delivery confirmed."""
import csv
import io

from app.models.orders import OrderLine
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.query import QuerySpec, parse_filter
from app.services.lots import LotService, ShippedService
from app.services.order_views import OrderService
from sqlalchemy import select, update
from tests.test_invoicing import OP, confirm, p6  # noqa: F401 — p6 is a fixture
from tests.test_orders import env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_orders_api import OPERATOR, READER, api  # noqa: F401 — api is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_traceability import shipped

ITEM = "scope=item_code:eq:CF0010"


async def two_orders(api):  # noqa: F811
    """Two Shopify orders: 9301 of CF0092, 9302 of CF0010 (its line moved to it)."""
    api.env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302)]
    await api.env.pull()
    orders = {o["source_order_number"]: o for o in (await api.get("/api/v1/orders", headers=READER)).json()["items"]}
    async with api.env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(update(OrderLine).where(OrderLine.order_id == orders["9302"]["id"]).values(item_code="CF0010"))
        await u.commit()
    return orders["9301"], orders["9302"]


async def test_the_scope_bounds_the_rows_the_count_and_the_export(api):  # noqa: F811
    a, b = await two_orders(api)
    page = (await api.get(f"/api/v1/orders?{ITEM}", headers=READER)).json()
    assert [o["id"] for o in page["items"]] == [b["id"]] and page["total"] == 1
    assert (await api.get(f"/api/v1/orders/count?{ITEM}", headers=READER)).json() == {"total": 1}
    # A search, a filter: inside the scope only (a filter of another item finds nothing, never the other order).
    assert (await api.get(f"/api/v1/orders/count?{ITEM}&search=9301", headers=READER)).json() == {"total": 0}
    assert (await api.get(f"/api/v1/orders/count?{ITEM}&filter=item_code:eq:CF0092", headers=READER)).json() == {"total": 0}
    assert (await api.get("/api/v1/orders/count?filter=item_code:in:CF0092|CF0010", headers=READER)).json() == {"total": 2}
    r = await api.get(f"/api/v1/orders/export.csv?{ITEM}", headers=READER)
    assert r.status_code == 200 and len(list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))) == 1
    # Only "has a line with": a negative would read as "has a line that is not", which no one means.
    r = await api.get("/api/v1/orders?scope=item_code:ne:CF0010", headers=READER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.unsupported_operator"
    r = await api.get("/api/v1/orders?scope=nope:eq:1", headers=READER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.unknown_field"


async def test_a_removed_line_does_not_count(api):  # noqa: F811
    _, b = await two_orders(api)
    async with api.env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(update(OrderLine).where(OrderLine.order_id == b["id"]).values(removed=True))
        await u.commit()
    assert (await api.get(f"/api/v1/orders/count?{ITEM}", headers=READER)).json() == {"total": 0}


async def test_an_action_on_chosen_records_outside_the_scope_is_refused_and_does_nothing(api):  # noqa: F811
    a, b = await two_orders(api)
    body = {"ids": {str(a["id"]): a["version"]}, "dry_run": True, "scope": ["item_code:eq:CF0010"]}
    r = await api.post("/api/v1/orders/actions/recheck", json=body, headers=OPERATOR)
    assert r.status_code == 409 and r.json()["error"]["code"] == "scope.outside" and r.json()["error"]["details"]["ids"] == [a["id"]]
    inside = {**body, "ids": {str(b["id"]): b["version"]}}
    assert (await api.post("/api/v1/orders/actions/recheck", json=inside, headers=OPERATOR)).status_code == 200
    # "All matching": the scope rides in the list's own query; only what it selects is a target.
    out = (await api.post("/api/v1/orders/actions/recheck", json={"query": ITEM, "dry_run": True}, headers=OPERATOR)).json()
    assert [x["id"] for x in out["results"]] == [b["id"]]
    assert (await api.post("/api/v1/orders/actions/recheck", json={**body, "scope": ["x"] * 4}, headers=OPERATOR)).status_code == 422


async def test_invoices_and_transfers_have_a_line_with_item_too(api):  # noqa: F811
    for lst in ("invoices", "transfers"):
        r = await api.get(f"/api/v1/{lst}/count?{ITEM}", headers=READER)
        assert r.status_code == 200 and r.json() == {"total": 0}, lst
        meta = (await api.get(f"/api/v1/{lst}/meta", headers=READER)).json()
        f = next(f for f in meta["fields"] if f["name"] == "item_code")
        assert (f["label"], f["operators"], f["in_row"]) == ("Has a line with item", ["eq", "in"], False), lst


async def test_shipped_is_the_applied_945_rows_with_the_customer_and_the_delivery(p6):  # noqa: F811
    a = await shipped(p6, "PO-1")
    b = await shipped(p6, "PO-2", rows=["LX*1", "W12*CP*60*60*0*EA**VN*CF0092", "N9*LT*LOT-C", "DTM*036*20280519"])
    async with p6.factory(SYSTEM_ACTOR) as u:  # PO-2's 945 kept only (a second one): in Lots, not in Shipped
        await u.session.execute(update(Shipment).where(Shipment.order_id == b.id).values(applied=False))
        await u.commit()
    async with p6.factory(OP) as u:
        lots = await LotService(u).list(QuerySpec())
        rows = await ShippedService(u).list(QuerySpec(sort=[]))
        scoped = await ShippedService(u).list(QuerySpec(scope=[parse_filter("lot:eq:LOT-A")]))
    assert {r.lot for r in lots.items} == {"LOT-A", "LOT-B", "LOT-C"}
    assert sorted((r.lot, r.order_ref, r.customer, r.delivery_confirmed) for r in rows.items) == [
        ("LOT-A", a.source_reference, "Loblaws", False), ("LOT-B", a.source_reference, "Loblaws", False)]
    assert [r.lot for r in scoped.items] == ["LOT-A"]
    # A Shopify customer: the initial of the ship-to name and the city, never the name.
    async with p6.factory(SYSTEM_ACTOR) as u:
        from app.models.orders import Order
        await u.session.execute(update(Order).where(Order.id == a.id).values(source_type="SHOPIFY"))
        await u.commit()
    await p6.inv.advance()
    await confirm(p6, a.id)
    async with p6.factory(OP) as u:
        rows = await ShippedService(u).list(QuerySpec())
    assert {(r.customer, r.delivery_confirmed) for r in rows.items} == {("Shopify · L., Ajax ON", True)}


async def test_shipped_over_http_is_read_with_sales_read(api):  # noqa: F811
    assert (await api.get("/api/v1/shipped", headers=READER)).status_code == 200
    meta = (await api.get("/api/v1/shipped/meta", headers=READER)).json()
    names = {f["name"] for f in meta["fields"]}
    assert {"customer", "delivery_confirmed", "lot", "item_code"} <= names and not {"applied", "shadow"} & names
    assert meta["refs"] == {"item": {"readable": True}, "lot": {"readable": True}, "order": {"readable": True}}
    assert (await api.get("/api/v1/shipped?scope=item_code:eq:CF0092", headers=READER)).status_code == 200


def test_the_scope_is_part_of_the_query_spec():
    spec = QuerySpec(scope=[parse_filter("item_code:eq:CF0010")])
    assert spec.scope[0].field == "item_code"
    assert OrderService.repo_cls.filterable["item_code"].through is not None
    assert select  # imported for readers who extend this module
