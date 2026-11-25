"""A record before its parts (V1, 2026-11-25): what writes a part locks its root first, as every other path — a customer's return (944),
the Shopify finance, a 945 that joins a warehouse order, the rows' links. Two real sessions each time: one holds the root and goes on
to its parts; the other must wait for it holding none of them, and then works on what is there."""
import asyncio
import collections
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.models.config import Item
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import StockCount, StockCountLine
from app.platform.actors import SYSTEM_ACTOR
from app.scripts import backfill_count_picked
from app.scripts.base import ScriptArgs
from app.scripts.import_radiant_shipments import Import
from tests.test_order_from_945 import a_945, lines, make
from tests.test_orders import OP, T0, env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_outbound import sent_order
from tests.test_returns_944 import ra_944
from tests.test_shadow_shipments import PART_A, shadow_on
from tests.test_shipments import p5, store_945, x945  # noqa: F401 — p5 is a fixture
from tests.test_shopify_finance import SALE, financed
from tests.test_transfers import p7, store  # noqa: F401 — p7 is a fixture


async def meets(factory, root, root_id, part, ids, coming, meanwhile=None):
    """`coming` waits for the root, holding no part of it; the holder writes the parts and commits; `coming` ends."""
    async with factory(OP) as u:
        s = u.session
        await s.get(root, root_id, with_for_update=True)
        task = asyncio.create_task(coming())
        await asyncio.sleep(0.6)
        free = set((await s.execute(sa.select(part.id).where(part.id.in_(ids)).with_for_update(skip_locked=True))).scalars())
        if task.done() or free != set(ids):
            await u.rollback()
            await asyncio.wait({task}, timeout=5)
            pytest.fail(f"it did not wait for the {root.__name__}, or held its parts meanwhile ({set(ids) - free})")
        if meanwhile:
            await meanwhile(s)
        await u.commit()
    return await asyncio.wait_for(task, 10)


async def ids_of(q, table, **where):
    k, v = next(iter(where.items()))
    return [r["id"] for r in await q(f"SELECT id FROM {table} WHERE {k} = :v ORDER BY id", v=v)]


async def version(q, table, id):
    return (await q(f"SELECT version FROM {table} WHERE id = :i", i=id))[0]["version"]


async def test_a_customer_s_return_944(p7):  # noqa: F811
    p7.edi.env.shop.orders = [shopify_order(1, 9301)]  # 12 EA
    await p7.edi.env.pull()
    o, _ = await p7.edi.env.order(1)
    await store(p7, "944_EDI", ra_944("RA CRM9301", qty=6), "944-ra.edi")
    parts = await ids_of(p7.q, "sales_order_lines", order_id=o.id)
    seen = await version(p7.q, "sales_orders", o.id)

    async def person(s):
        (await s.get(OrderLine, parts[0])).returned_ea = 3
    await meets(p7.factory, Order, o.id, OrderLine, parts, p7.tr.process_944s, person)
    assert (await p7.q("SELECT returned_ea FROM sales_order_lines WHERE id = :i", i=parts[0])) == [{"returned_ea": 9}]  # 3 + 6, not 6
    assert await version(p7.q, "sales_orders", o.id) == seen + 2  # the person's and the 944's


@pytest.mark.parametrize("path", ["the refresh", "the boundary order of a page"])
async def test_the_shopify_finance(env, path):  # noqa: F811
    env.shop.orders = [financed()]
    env.shop.transactions_by_order["1"] = [SALE]
    await env.pull()
    o, _ = await env.order(1)
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_orders SET finance_updated_at = NULL, net_amount = NULL"))
        if path == "the refresh":
            await u.session.execute(sa.text("UPDATE sales_connections SET cursor_updated_at = :c"), {"c": T0 + timedelta(days=30)})
        await u.commit()
    parts = await ids_of(env.q, "sales_order_lines", order_id=o.id)

    async def person(s):
        (await s.get(OrderLine, parts[0])).returned_ea = 1
    r = await meets(env.factory, Order, o.id, OrderLine, parts, env.pull, person)
    assert r.status == "ok" and r.finance_failed == 0
    now, _ = await env.order(1)
    assert now.net_amount is not None and now.finance_updated_at == now.source_updated_at
    assert (await env.q("SELECT returned_ea FROM sales_order_lines WHERE id = :i", i=parts[0])) == [{"returned_ea": 1}]


async def test_an_older_finance_read_before_the_lock_never_overwrites_a_newer_one(env):  # noqa: F811
    env.shop.orders = [financed()]
    env.shop.transactions_by_order["1"] = [SALE]
    await env.pull()
    o, _ = await env.order(1)
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_orders SET finance_updated_at = NULL, net_amount = 5"))
        await u.session.execute(sa.text("UPDATE sales_connections SET cursor_updated_at = :c"), {"c": T0 + timedelta(days=30)})
        await u.commit()
    parts = await ids_of(env.q, "sales_order_lines", order_id=o.id)

    async def newer(s):
        (await s.get(Order, o.id)).source_updated_at = datetime(2027, 1, 1, tzinfo=timezone.utc)  # a newer state came in
    r = await meets(env.factory, Order, o.id, OrderLine, parts, env.pull, newer)
    assert (r.finance_refreshed, r.finance_failed) == (0, 0)
    now, _ = await env.order(1)
    assert now.net_amount == 5 and now.finance_updated_at is None  # left for the next pass


async def test_a_shadow_945_that_joins_a_warehouse_order(p5):  # noqa: F811
    o = await make(p5, await a_945(p5, key="001008561a", rows=["LX*1", "W12*CC*12*7*0*EA**VN*CF0092"]))
    parts = await ids_of(p5.q, "sales_order_lines", order_id=o.id)
    await store_945(p5, x945("001008561b", ["LX*1", "W12*CC*12*5*0*EA**VN*CF0092", "LX*2", "W12*CC*3*3*0*EA**VN*CF0010"],
                             [], ctl="000000778"), "b.edi")
    async def person(s):
        (await s.get(OrderLine, parts[0])).problem = "looked at"
    await meets(p5.factory, Order, o.id, OrderLine, parts, p5.ships.process_945s, person)
    assert await lines(p5, o.id) == [(1, "CF0092", 12), (2, "CF0010", 3)]


@pytest.mark.parametrize("which", ["link", "relink"])
async def test_the_shadow_rows_links(p5, which):  # noqa: F811
    await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301a", PART_A, ctl="000000783"), "945-a.edi")
    await p5.ships.process_945s()
    [sid] = [r["id"] for r in await p5.q("SELECT id FROM sales_shipments")]
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(ShipmentRow).values(order_line_id=None))
        await u.commit()
    parts = await ids_of(p5.q, "sales_shipment_rows", shipment_id=sid)
    seen = await version(p5.q, "sales_shipments", sid)

    async def run():
        async with p5.factory(SYSTEM_ACTOR) as u:
            out = await (p5.ships.link_shadow_rows if which == "link" else p5.ships.relink_shadow_rows)(u, dry_run=False)
            await u.commit()
            return out
    async def person(s):
        (await s.get(Shipment, sid)).carrier = "ZZZZ"
    out = await meets(p5.factory, Shipment, sid, ShipmentRow, parts, run, person)
    assert out["rows_linked" if which == "link" else "rows_moved"] == len(parts) >= 1
    assert await version(p5.q, "sales_shipments", sid) == seen + 2


async def test_the_picked_pallets_script(env, monkeypatch):  # noqa: F811
    from tests.test_stock_counts import SUMMARY
    from app.services.stock_counts import StockCountService
    async with env.factory(OP) as u:
        c = await StockCountService(u).create(name="i.csv", data=SUMMARY.encode(), counted_at=datetime.now(timezone.utc) - timedelta(hours=1), note=None)
        await u.commit()
    parts = await ids_of(env.q, "sales_stock_count_lines", count_id=c.id)
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(StockCountLine).values(picked={"1": 1}))
        await u.commit()
    monkeypatch.setattr(backfill_count_picked, "uow_factory", env.factory)

    async def person(s):
        (await s.get(StockCount, c.id)).note = "looked at"
    out = await meets(env.factory, StockCount, c.id, StockCountLine, parts, lambda: backfill_count_picked.main(ScriptArgs(("--apply",))), person)
    assert out["outcome"]["line set"] == len(parts) and await version(env.q, "sales_stock_counts", c.id) == 3


async def test_the_radiant_shipments_script(p5):  # noqa: F811
    o, _ = await sent_order(p5.edi)
    parts = await ids_of(p5.q, "sales_order_lines", order_id=o.id)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.get(Order, o.id, with_for_update=True)
        (await u.session.get(OrderLine, parts[0])).shipped_ea = 1
        await u.commit()

    async def shipment(*a):  # noqa: ARG001
        return None

    async def old():
        async with p5.factory(SYSTEM_ACTOR) as u:
            me = SimpleNamespace(s=u.session, n=collections.Counter(), returned_944=set(), shipment=shipment)
            await Import.ship_old(me, {"rows": [{"item": "CF0092", "shipped": 2}]}, o.id)
            await u.commit()

    async def person(s):
        (await s.get(Order, o.id)).notes = "looked at"
    await meets(p5.factory, Order, o.id, OrderLine, parts, old, person)
    assert (await p5.q("SELECT shipped_ea FROM sales_order_lines WHERE id = :i", i=parts[0])) == [{"shipped_ea": 3}]


async def test_the_radiant_shipments_script_links_a_945(p5):  # noqa: F811
    o, _ = await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301a", PART_A, ctl="000000783"), "945-a.edi")
    await p5.ships.process_945s()
    [sid] = [r["id"] for r in await p5.q("SELECT id FROM sales_shipments")]
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(ShipmentRow).values(order_line_id=None))
        await u.commit()
    parts = await ids_of(p5.q, "sales_shipment_rows", shipment_id=sid)

    async def link():
        async with p5.factory(SYSTEM_ACTOR) as u:
            sh, order = await u.session.get(Shipment, sid), await u.session.get(Order, o.id)
            [ln] = (await u.session.execute(sa.select(OrderLine).where(OrderLine.order_id == o.id))).scalars()
            await Import.link_945(SimpleNamespace(s=u.session, n=collections.Counter()), sh, order, {"CF0092": ln})
            await u.commit()

    async def person(s):
        (await s.get(Shipment, sid)).carrier = "ZZZZ"
    await meets(p5.factory, Shipment, sid, ShipmentRow, parts, link, person)
    assert await p5.q("SELECT 1 FROM sales_shipment_rows WHERE order_line_id IS NULL") == []


async def test_an_item_s_units_and_its_lock(env):  # noqa: F811
    from app.schemas.items import ItemUpdate
    from app.services.items import ItemService
    from app.models.config import ItemUnit
    [item] = await ids_of(env.q, "sales_items", item_code="CF0092")
    parts = await ids_of(env.q, "sales_item_units", item_code="CF0092")
    seen = await version(env.q, "sales_items", item)

    async def edit():
        async with env.factory(OP) as u:
            await ItemService(u).update(item, ItemUpdate(units=[{"unit": "EA", "factor_ea": "1"}, {"unit": "CASE", "factor_ea": "7"}]), seen)
            await u.commit()
    await meets(env.factory, Item, item, ItemUnit, parts, edit)
    assert await version(env.q, "sales_items", item) == seen + 1
