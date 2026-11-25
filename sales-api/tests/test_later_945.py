"""A later 945 of a Shopify order (user 2026-11-25): what the first left is added by the pass; more than ordered, or one already
held, by a person in Compare the 945s. Nothing goes to Shopify for it. The marks (n shipments, Over shipped) are derived."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.models.payments import OrderRefund, OrderRefundLine
from app.platform.actors import SYSTEM_ACTOR
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.errors import BusinessError
from app.platform.query import Filter, QuerySpec
from app.schemas.orders import ReturnedIn
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from app.services.return_total import refuse_more_than_shipped
from app.services.stock import StockItemService, StockMoveService, sync
from tests import acting
from tests.test_orders import OP, T0, env  # noqa: F401 — a fixture
from tests.test_outbound import a_paid_order, ack997, docs, status, store_997
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import live_store, p5, store_945, tasks, writes, x945  # noqa: F401 — p5 is a fixture

LINES = [{"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 2, "price": "50", "requires_shipping": True},
         {"id": 12, "sku": "CF0010-SKU", "title": "Other", "variant_title": "Can", "quantity": 4, "price": "9", "requires_shipping": True}]
CAN = "LX*2"
HEAD = ["N1*ST*A B", "N4*Montreal*QC*H2X 1Y4*CA", "DTM*011*20261102", "N9*2I*2Z-TRACK"]  # the second parcel's number


@pytest.fixture(autouse=True)
async def _empty_ledger(db_engine):
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE sales_stock_moves RESTART IDENTITY"))


def row(item, n, lot="L1"):
    return [f"W12*CP*{n}*{n}*0*EA**VN*{item}", f"N9*LT*{lot}"]


async def short_order(t, first=(("CF0092", 12),), fulfil=True):
    """An order of CF0092 12 EA and CF0010 4 EA, fulfilled at its 997, shipped by a first 945 (by default without CF0010)."""
    await live_store(t)
    await a_paid_order(t.edi, lines=LINES)
    t.shop.fulfillment_orders_by_order["1"] = [{"id": 501, "status": "open", "line_items": [
        {"id": 601, "line_item_id": 11, "fulfillable_quantity": 2}, {"id": 602, "line_item_id": 12, "fulfillable_quantity": 4}]}]
    await t.edi.out.queue_due()
    await t.edi.worker.run_once()
    [d] = [x for x in await docs(t.edi) if x["file_status"] == "Sent"]
    if fulfil:
        await store_997(t.edi, ack997(d["st02"]))
        await t.edi.out.process_997s()
        await t.worker.run_once()
    await store_945(t, x945("9301", ["LX*1", *[s for i, n in first for s in row(i, n)]]), "first.edi")
    t.edi.clock.now = T0 + timedelta(days=5)
    await t.ships.process_945s()
    await t.worker.run_once()
    [o] = await t.q("SELECT id FROM sales_orders WHERE source_order_number = '9301'")
    if fulfil:
        await patch(t, o["id"], shopify_fulfillment_status="fulfilled")  # as the next pull reads it
    t.shop.calls.clear()
    return o["id"]


async def patch(t, oid, **kw):
    async with t.factory(SYSTEM_ACTOR) as u:
        o = await u.session.get(Order, oid)
        for k, v in kw.items():
            setattr(o, k, v)
        await u.session.flush()
        await u.commit()


async def second(t, rows, name="second.edi", ctl="000000781", key="9301a"):
    await store_945(t, x945(key, ["LX*1", *[s for r in rows for s in row(*r)]], header=HEAD, ctl=ctl), name)
    r = await t.ships.process_945s()
    assert r.failed == 0, r.detail
    return r


async def lines(t, oid):
    return {x["item_code"]: x["shipped_ea"] for x in await t.q("SELECT item_code, shipped_ea FROM sales_order_lines WHERE order_id = :o", o=oid)}


async def applied(t):
    return [x["applied"] for x in await t.q("SELECT applied FROM sales_shipments ORDER BY id")]


async def open_tasks(t):
    return {x["type"].removeprefix("sales_") for x in await t.q("SELECT type FROM tasks WHERE NOT is_completed AND type LIKE 'sales_%'")}


async def read(t, oid):
    async with t.factory(OP) as u:
        return await OrderService(u).detail(oid)


async def act(t, action, oid, shipment_id, *, reason="the warehouse confirmed it", version=None):
    async with t.factory(OP) as u:
        if version is None:
            version = (await u.session.execute(sa.select(Order.version).where(Order.id == oid))).scalar_one()
        out = await run_action(u, ORDER_ACTIONS, action, ActionIn(ids={oid: version}, reason=reason, input={"shipment_id": shipment_id}),
                               SimpleNamespace(tasks=t.edi.env.tasks))
        await u.commit()
    return out.results[0]


async def no_write(t):
    """Nothing was queued for, or written to, Shopify since the first 945."""
    await t.worker.run_once()
    assert writes(t) == []
    later = await t.q("SELECT shopify_status, shopify_fulfillment_id FROM sales_shipments WHERE id > 1 AND applied")
    assert all(x == {"shopify_status": "Not needed", "shopify_fulfillment_id": None} for x in later)
    assert await t.q("SELECT 1 FROM sales_outbox WHERE kind LIKE 'shopify%' AND status = 'PENDING'") == []


# ── by the pass ──────────────────────────────────────────────────────────────

async def test_the_part_the_first_left_is_added_and_the_tasks_close(p5):  # noqa: F811
    oid = await short_order(p5)
    assert await open_tasks(p5) >= {"short_shipment"}
    r = await second(p5, [("CF0010", 4)])
    assert (r.parts, r.second, r.repeats) == (1, 0, 0)
    assert await status(p5.edi, oid) == F.SHIPPED and await applied(p5) == [True, True]
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4}
    assert not await open_tasks(p5) & {"short_shipment", "second_945", "945_validation"}
    [r2] = await p5.q("SELECT l.item_code FROM sales_shipment_rows r JOIN sales_order_lines l ON l.id = r.order_line_id WHERE r.shipment_id = 2")
    assert r2["item_code"] == "CF0010"  # its row is on its line
    d = await read(p5, oid)
    assert (d.short_shipped, d.applied_945s, d.over_shipped_ea, d.later_945) == (False, 2, 0, None)
    assert [x.number for sh in d.shipments for x in sh.tracking_links] == ["1Z-TRACK", "2Z-TRACK"]  # both shown
    assert d.version > 1 and any("added to order" in x["message"] for x in await p5.q("SELECT message FROM sales_log WHERE step = '945'"))
    await no_write(p5)


async def test_a_part_that_leaves_something_says_what_and_a_third_brings_it(p5):  # noqa: F811
    oid = await short_order(p5, first=(("CF0092", 6),))
    await second(p5, [("CF0092", 4)])
    assert await lines(p5, oid) == {"CF0092": 10, "CF0010": 0}
    [x] = await tasks(p5, "short_shipment")
    assert "CF0092: 2 EA not shipped (10 of 12)" in x and "CF0010: 4 EA not shipped (0 of 4)" in x and "second.edi" in x
    d = await read(p5, oid)
    assert (d.short_shipped, d.applied_945s) == (True, 2)
    await second(p5, [("CF0092", 2), ("CF0010", 4)], "third.edi", "000000782", "9301b")
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4} and "short_shipment" not in await open_tasks(p5)
    d = await read(p5, oid)
    assert (d.short_shipped, d.applied_945s, d.over_shipped_ea) == (False, 3, 0)
    await no_write(p5)


async def test_a_part_before_the_mirror_says_fulfilled_asks_a_person_to_close_the_lines_in_shopify(p5):  # noqa: F811
    """Fulfilled by its first 945 (its 997 wrote nothing): the line it left is still open in Shopify, and stays a person's."""
    oid = await short_order(p5, fulfil=False)
    await patch(p5, oid, shopify_fulfillment_status="partial")
    await second(p5, [("CF0010", 4)])
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4}
    [x] = await tasks(p5, "short_shipment")
    assert "still open" in x and "by hand" in x and "short_shipment" in await open_tasks(p5)
    await no_write(p5)


async def test_more_than_the_940_is_held_by_the_job_with_why(p5):  # noqa: F811
    oid = await short_order(p5)
    r = await second(p5, [("CF0010", 5)])
    assert (r.parts, r.second) == (0, 1) and await status(p5.edi, oid) == F.ON_HOLD and await applied(p5) == [True, False]
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 0}
    [x] = await tasks(p5, "second_945")
    assert "CF0010: 1 EA above the 4 on the 940 (5 with this 945)" in x and "On Hold" in x
    [c] = (await p5.q("SELECT checks FROM sales_shipments WHERE id = 2"))[0]["checks"]
    assert "above the 4 on the 940" in c["message"]
    d = await read(p5, oid)
    assert (d.applied_945s, d.over_shipped_ea, d.later_945.kind) == (1, 0, "more")
    await no_write(p5)


async def refused(t, oid, says, rows=(("CF0010", 4),)):
    await second(t, rows)
    assert await status(t.edi, oid) != F.SHIPPED or await applied(t) == [True, False]
    assert await applied(t) == [True, False]
    assert (await lines(t, oid))["CF0010"] == 0
    [x] = await tasks(t, "second_945")
    assert says in x, x
    if await status(t.edi, oid) == F.ON_HOLD:
        for a in ("apply_part_945", "apply_more_945"):  # and a person is refused both
            r = await act(t, a, oid, 2)
            assert r.status == "skipped" and r.code == "order.later_945", r
    await no_write(t)


async def test_a_child_order_refuses_a_part_and_more(p5):  # noqa: F811
    oid = await short_order(p5)
    [src] = await p5.q("SELECT partner_code FROM sales_orders WHERE id = :o", o=oid)
    async with p5.factory(SYSTEM_ACTOR) as u:
        u.session.add(Order(partner_code=src["partner_code"], source_type="MANUAL", source_order_id="gift", fulfillment_status="Draft",
                            invoice_status="Not Required", first_received_at=T0, parent_order_id=oid, version=1))
        await u.commit()
    await refused(p5, oid, "an order was made from this one", rows=(("CF0010", 5),))


async def test_a_manual_adjustment_of_the_item_since_the_first_945_refuses_and_one_put_in_or_before_does_not(p5):  # noqa: F811
    oid = await short_order(p5)
    async with p5.factory(OP) as u:
        await StockMoveService(u).adjust(item_code="CF0010", lot="L1", qty_ea=3, reason="found")       # put in
        await StockMoveService(u).adjust(item_code="CF0092", lot=None, qty_ea=-3, reason="another item")
        await u.commit()
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_stock_moves SET at = '2026-12-01' WHERE source_type = 'adjustment'"))
        await u.commit()
    await second(p5, [("CF0010", 1)])
    assert await applied(p5) == [True, True]
    async with p5.factory(OP) as u:
        m = await StockMoveService(u).adjust(item_code="CF0010", lot=None, qty_ea=-3, reason="taken out by hand")
        await u.session.flush()
        await u.session.execute(sa.text("UPDATE sales_stock_moves SET at = '2026-10-01' WHERE id = :i"), {"i": m.id})  # before the first 945
        await u.commit()
    await second(p5, [("CF0010", 1, "L2")], "third.edi", "000000782", "9301b")
    assert await applied(p5) == [True, True, True]
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_stock_moves SET at = '2026-12-01' WHERE source_type = 'adjustment'"))
        await u.commit()
    await second(p5, [("CF0010", 2)], "fourth.edi", "000000783", "9301c")
    assert await applied(p5) == [True, True, True, False] and await status(p5.edi, oid) == F.ON_HOLD
    [x] = await tasks(p5, "second_945")
    assert "CF0010 was adjusted out by a person" in x


async def _line(t, oid, item):
    return (await t.q("SELECT id FROM sales_order_lines WHERE order_id = :o AND item_code = :i", o=oid, i=item))[0]["id"]


async def _refund(t, oid, line_id):
    async with t.factory(SYSTEM_ACTOR) as u:
        rf = OrderRefund(order_id=oid, shopify_id=900, refunded_at=T0, raw={})
        u.session.add(rf)
        await u.session.flush()
        u.session.add(OrderRefundLine(refund_id=rf.id, shopify_id=901, order_line_id=line_id, quantity=1))
        await u.commit()


async def test_a_refund_line_of_the_item_refuses_one_of_another_item_does_not(p5):  # noqa: F811
    oid = await short_order(p5)
    await _refund(p5, oid, await _line(p5, oid, "CF0010"))
    await refused(p5, oid, "a refund or a return is recorded for CF0010")


async def test_a_refund_of_another_item_does_not_refuse(p5):  # noqa: F811
    oid = await short_order(p5)
    await _refund(p5, oid, await _line(p5, oid, "CF0092"))
    await second(p5, [("CF0010", 4)])
    assert await applied(p5) == [True, True]


async def test_a_refund_line_of_no_known_line_refuses(p5):  # noqa: F811
    oid = await short_order(p5)
    await _refund(p5, oid, None)
    await refused(p5, oid, "a refund or a return is recorded for CF0010")


@pytest.mark.parametrize("how", ["on the order", "recorded"])
async def test_a_return_of_the_item_refuses(p5, how):  # noqa: F811
    oid = await short_order(p5, first=(("CF0092", 10),))
    lid = await _line(p5, oid, "CF0092")
    async with p5.factory(OP) as u:
        if how == "on the order":
            from app.platform.commands import ANY_VERSION
            await OrderService(u).set_returned(oid, ReturnedIn(reason="came back", lines=[{"id": lid, "returned_ea": 1}]), ANY_VERSION)
        else:
            from app.models.invoices import OrderReturn
            u.session.add(OrderReturn(order_id=oid, kind="Returned", reason="came back", lines=[{"order_line_id": lid, "qty_ea": 1}]))
        await u.commit()
    await second(p5, [("CF0092", 2)])
    assert await applied(p5) == [True, False]
    [x] = await tasks(p5, "second_945")
    assert "a refund or a return is recorded for CF0092" in x


@pytest.mark.parametrize("change,says", [
    (dict(historical=True), "old one"), (dict(source_type="MANUAL"), "only a Shopify order"), (dict(invoice_status="Blocked"), "Blocked"),
    (dict(source_cancelled_at=T0), "cancelled at source"), (dict(fulfillment_status="Cancelled"), "the order is Cancelled, not Shipped"),
])
async def test_what_the_order_is_refuses(p5, change, says):  # noqa: F811
    oid = await short_order(p5)
    await patch(p5, oid, **change)
    key = "9301a" if "source_type" not in change else f"{oid + 1000000}"
    if "source_type" in change:  # a manual order is found by its internal number
        key = str((await p5.q("SELECT order_number n FROM sales_orders WHERE id = :o", o=oid))[0]["n"])
    await second(p5, [("CF0010", 4)], key=key)
    assert await applied(p5) == [True, False] and (await lines(p5, oid))["CF0010"] == 0
    [x] = await tasks(p5, "second_945")
    assert says in x
    if "fulfillment_status" in change:
        assert await status(p5.edi, oid) == F.CANCELLED


async def test_an_invoice_or_a_confirmation_refuses(p5):  # noqa: F811
    from app.models.invoices import DeliveryConfirmation
    oid = await short_order(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        u.session.add(DeliveryConfirmation(order_id=oid, confirmed_date=T0.date()))
        await u.commit()
    await second(p5, [("CF0010", 4)])
    assert await applied(p5) == [True, False]
    [x] = await tasks(p5, "second_945")
    assert "already settled or stopped" in x


async def test_an_order_whose_940_sales_did_not_send_and_a_row_of_another_item_are_held(p5):  # noqa: F811
    oid = await short_order(p5)
    await second(p5, [("CF0010", 4), ("CF0777", 1)])
    [x] = await tasks(p5, "second_945")
    assert "rows match no line of the 940: CF0777 1 EA" in x and await applied(p5) == [True, False]
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_documents SET sent_at = NULL WHERE kind = '940_EDI'"))
        await u.commit()
    await second(p5, [("CF0010", 4)], "third.edi", "000000782", "9301b")
    assert "Sales sent no 940" in (await tasks(p5, "second_945"))[0]
    assert (await lines(p5, oid))["CF0010"] == 0


async def test_a_resend_of_the_first_the_second_or_a_held_one_changes_nothing(p5):  # noqa: F811
    oid = await short_order(p5)
    await second(p5, [("CF0010", 4)])
    await second(p5, [("CF0010", 4)], "again.edi", "000000782")
    await store_945(p5, x945("9301", ["LX*1", *row("CF0092", 12)], ctl="000000783"), "first-again.edi")
    r = await p5.ships.process_945s()
    assert r.repeats == 1 and await applied(p5) == [True, True, False, False]
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4} and await status(p5.edi, oid) == F.SHIPPED
    await second(p5, [("CF0010", 4, "L9")], "corrected.edi", "000000784", "9301b")  # another lot: more than ordered
    assert await status(p5.edi, oid) == F.ON_HOLD
    before = await p5.q("SELECT count(*) n FROM sales_log WHERE step = '945 validation'")
    r = await second(p5, [("CF0010", 4, "L9")], "corrected-again.edi", "000000785", "9301b")
    assert (r.repeats, r.second) == (1, 0) and await p5.q("SELECT count(*) n FROM sales_log WHERE step = '945 validation'") == before
    d = await read(p5, oid)
    assert (d.applied_945s, d.later_945.kind, d.later_945.shipment_id) == (2, "more", 5)  # the resend is not the one that waits
    await no_write(p5)


async def test_one_file_with_two_sets_of_the_order(p5):  # noqa: F811
    """Each set is a 945 of its own: a part and then the rest; a held one and then another, the order locked again after its change."""
    oid = await short_order(p5, first=(("CF0092", 12),))

    async def file(sets, name, ctl):
        data = x945("9301a", ["LX*1", *row(*sets[0])], header=HEAD, ctl=ctl).decode()
        st, rest = data.index("ST*945*0001"), data.index("GE*1*777")
        more = data[st:rest].replace("ST*945*0001", "ST*945*0002").replace("*0001~\n", "*0002~\n").replace(
            "".join(f"{x}~\n" for x in row(*sets[0])), "".join(f"{x}~\n" for x in row(*sets[1])))
        await store_945(p5, (data[:rest] + more + data[rest:].replace("GE*1*", "GE*2*")).encode(), name)
        r = await p5.ships.process_945s()
        assert r.failed == 0, r.detail
        return r
    r = await file([("CF0010", 1), ("CF0010", 2)], "two.edi", "000000781")
    assert r.parts == 2 and await applied(p5) == [True, True, True] and (await lines(p5, oid))["CF0010"] == 3
    r = await file([("CF0010", 5), ("CF0010", 6)], "two-more.edi", "000000782")
    assert r.second == 2 and await applied(p5) == [True, True, True, False, False] and await status(p5.edi, oid) == F.ON_HOLD
    await no_write(p5)


async def test_the_shadow_run_stores_and_compares_as_before(p5):  # noqa: F811
    oid = await short_order(p5)
    await store_945(p5, x945("9301a", ["LX*1", *row("CF0010", 4)], ctl="000000781"), "second.edi", shadow=True)
    r = await p5.ships.process_945s()
    assert (r.shadow, r.parts) == (1, 0) and await applied(p5) == [True, False] and (await lines(p5, oid))["CF0010"] == 0
    assert (await read(p5, oid)).applied_945s == 1


# ── by a person (Compare the 945s) ───────────────────────────────────────────

async def held_part(t):
    """As SHP-9649: a missing part that was held before the rule (the job's refusal is lifted after)."""
    oid = await short_order(t)
    await patch(t, oid, invoice_status="Blocked")
    await second(t, [("CF0010", 4)])
    assert await status(t.edi, oid) == F.ON_HOLD
    await patch(t, oid, invoice_status="Not Required")
    return oid


async def test_a_held_part_is_pressed_once(p5):  # noqa: F811
    oid = await held_part(p5)
    d = await read(p5, oid)
    assert (d.later_945.kind, d.later_945.refused, d.later_945.shipment_id) == ("part", None, 2)
    assert (await act(p5, "apply_part_945", oid, 2, version=1)).code == "entity.stale"          # an old page
    assert (await act(p5, "apply_part_945", oid, 1)).code == "order.later_945"                   # another 945 than the one that waits
    assert (await act(p5, "apply_more_945", oid, 2)).status == "skipped"                         # it is not more than ordered
    assert await applied(p5) == [True, False]
    r = await act(p5, "apply_part_945", oid, 2, reason="Radiant: the can left later")
    assert r.status == "done", r
    assert await status(p5.edi, oid) == F.SHIPPED and await lines(p5, oid) == {"CF0092": 12, "CF0010": 4} and await applied(p5) == [True, True]
    [x] = await p5.q("SELECT l.item_code, r.needs_review FROM sales_shipment_rows r JOIN sales_order_lines l ON l.id = r.order_line_id WHERE r.shipment_id = 2")
    assert x == {"item_code": "CF0010", "needs_review": False}  # its row is on its line
    assert not await open_tasks(p5) & {"short_shipment", "second_945", "945_validation"}
    [a] = await p5.q("SELECT reason FROM sales_audit WHERE entity = 'sales_orders' AND entity_id = :o AND reason LIKE '945 %' LIMIT 1", o=str(oid))
    assert "Radiant: the can left later" in a["reason"] and "the part the first 945 left" in a["reason"]
    assert "applied by hand" in str((await p5.q("SELECT checks FROM sales_shipments WHERE id = 2"))[0]["checks"])
    r = await act(p5, "apply_part_945", oid, 2)
    assert r.status == "skipped" and "waits for a decision" in r.reason
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4}
    await no_write(p5)


async def test_the_press_over_http(p5):  # noqa: F811
    """The route people reach: without the permission, from an old page, once, and again (skipped: a double click)."""
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_uow_factory
    from app.main import app
    from app.runtime import get_runtime
    from tests.test_orders_api import GRANTS, OPERATOR, READER
    oid = await held_part(p5)

    async def matrix(session, actor, key):  # noqa: ARG001
        return key in GRANTS.get(actor.role or "", set())
    p5.factory.permissions = matrix
    app.dependency_overrides[get_uow_factory] = lambda: p5.factory
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(tasks=p5.edi.env.tasks)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            async def press(version, headers=OPERATOR, **kw):
                return await c.post("/api/v1/orders/actions/apply_part_945", headers=headers, json={
                    "ids": {str(oid): version}, "reason": "confirmed", "input": {"shipment_id": 2}, **kw})
            d = (await c.get(f"/api/v1/orders/{oid}", headers=READER)).json()
            assert d["later_945"] == {"shipment_id": 2, "kind": "part", "refused": None, "items": [
                {"item_code": "CF0010", "carried_ea": 4, "earlier_ea": 0, "this_ea": 4, "above_ea": 0}]}
            assert (await press(d["version"], READER)).status_code == 403
            assert (await press(d["version"], reason="")).status_code == 422
            assert (await press(d["version"], input={})).status_code == 422
            assert (await press(None)).status_code == 422
            [x] = (await press(d["version"] - 1)).json()["results"]
            assert (x["status"], x["code"]) == ("failed", "entity.stale")
            [x] = (await press(d["version"])).json()["results"]
            assert x["status"] == "done" and x["version"] > d["version"]
            [x] = (await press(d["version"])).json()["results"]
            assert x["status"] == "skipped"
            rows = (await c.get("/api/v1/orders?filter=applied_945s:gte:2", headers=READER)).json()
            assert [(o["applied_945s"], o["over_shipped_ea"]) for o in rows["items"]] == [(2, 0)]
            meta = {f["name"]: f for f in (await c.get("/api/v1/orders/meta", headers=READER)).json()["fields"]}
            assert meta["applied_945s"]["filterable"] and meta["over_shipped_ea"]["filterable"] and meta["over_shipped_ea"]["label"] == "Over shipped EA"
    finally:
        app.dependency_overrides.clear()
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4}


async def test_a_reason_and_one_record_at_a_time(p5):  # noqa: F811
    oid = await held_part(p5)
    with pytest.raises(BusinessError) as e:
        await act(p5, "apply_part_945", oid, 2, reason=" ")
    assert e.value.code == "reason.required"
    async with p5.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await run_action(u, ORDER_ACTIONS, "apply_more_945", ActionIn(ids={oid: 1, oid + 1: 1}, reason="why not", input={"shipment_id": 2}), None)
        assert e.value.code == "action.single"


async def test_more_than_ordered_is_pressed_and_shows_over_shipped(p5):  # noqa: F811
    oid = await short_order(p5)
    await second(p5, [("CF0010", 5)])
    d = await read(p5, oid)
    [i] = d.later_945.items
    assert (i.item_code, i.carried_ea, i.earlier_ea, i.this_ea, i.above_ea) == ("CF0010", 4, 0, 5, 1)
    assert (await act(p5, "apply_part_945", oid, 2)).status == "skipped"
    r = await act(p5, "apply_more_945", oid, 2, reason="an extra can by mistake")
    assert r.status == "done", r
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 5} and await status(p5.edi, oid) == F.SHIPPED
    d = await read(p5, oid)
    assert (d.applied_945s, d.over_shipped_ea, d.short_shipped) == (2, 1, False)
    assert {ln.item_code: ln.state for ln in d.lines} == {"CF0092": "Shipped", "CF0010": "Over shipped"}
    await no_write(p5)
    # The returns rule compares with the new total: 5, not the 4 of the 940.
    lid = await _line(p5, oid, "CF0010")
    async with p5.factory(OP) as u:
        await refuse_more_than_shipped(u.session, oid, recording=[{"order_line_id": lid, "qty_ea": 5}])
        with pytest.raises(BusinessError) as e:
            await refuse_more_than_shipped(u.session, oid, recording=[{"order_line_id": lid, "qty_ea": 6}])
        assert e.value.code == "return.more_than_shipped" and "more than the 5 EA shipped" in e.value.message
    async with p5.factory(OP) as u:
        await OrderService(u).set_returned(oid, ReturnedIn(reason="all back", lines=[{"id": lid, "returned_ea": 5}]), d.version)
        await u.commit()


async def test_a_press_on_an_order_that_is_not_held_or_is_zohos_is_refused(p5):  # noqa: F811
    oid = await held_part(p5)
    await patch(p5, oid, source_cancelled_at=T0)  # cancelled at source after the hold
    r = await act(p5, "apply_part_945", oid, 2)
    assert r.status == "skipped" and "cancelled at source" in r.reason
    await patch(p5, oid, source_cancelled_at=None, shadow=True)
    r = await act(p5, "apply_part_945", oid, 2)
    assert (r.status, r.code) == ("skipped", "order.shadow") and (await read(p5, oid)).later_945 is None
    await patch(p5, oid, shadow=False, fulfillment_status_before_hold="940 Sent")
    assert (await act(p5, "apply_part_945", oid, 2)).status == "skipped"
    assert await applied(p5) == [True, False]


async def test_the_press_looks_again_at_what_it_is_under_its_locks(p5):  # noqa: F811
    from app.domain.later_part import MORE
    from app.services import later_part
    oid = await held_part(p5)  # a part
    async with p5.factory(OP) as u:
        o = await u.session.get(Order, oid, with_for_update=True)
        with pytest.raises(BusinessError) as e:
            await later_part.press(u, p5.edi.env.tasks, o, 2, MORE, "why not")
        assert e.value.code == "order.later_945"
        await u.rollback()
    assert await applied(p5) == [True, False]


async def test_two_lines_of_one_item_the_part_fills_the_line_that_is_short(p5):  # noqa: F811
    await live_store(p5)
    await a_paid_order(p5.edi, lines=[LINES[0], {**LINES[0], "id": 13, "variant_title": "Can", "quantity": 4}])  # 12 EA and 4 EA of CF0092
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9301", ["LX*1", *row("CF0092", 13)]), "first.edi")
    p5.edi.clock.now = T0 + timedelta(days=5)
    await p5.ships.process_945s()
    await second(p5, [("CF0092", 3)])
    assert [x["shipped_ea"] for x in await p5.q("SELECT shipped_ea FROM sales_order_lines ORDER BY line_no")] == [12, 4]
    [d] = await p5.q("SELECT l.line_no FROM sales_shipment_rows r JOIN sales_order_lines l ON l.id = r.order_line_id WHERE r.shipment_id = 2")
    assert d["line_no"] == 13
    [o] = await p5.q("SELECT id FROM sales_orders")
    assert (await read(p5, o["id"])).over_shipped_ea == 0


async def test_two_different_945s_that_wait_are_not_added(p5):  # noqa: F811
    oid = await held_part(p5)
    await second(p5, [("CF0010", 3)], "third.edi", "000000782", "9301b")
    d = await read(p5, oid)
    assert d.later_945.kind is None and "second.edi" in d.later_945.refused and d.later_945.shipment_id == 3
    for a in ("apply_part_945", "apply_more_945"):
        assert (await act(p5, a, oid, 3)).status == "skipped"
    assert await applied(p5) == [True, False, False]


async def test_keeping_the_applied_one_still_works_and_queues_nothing_for_a_later_part(p5):  # noqa: F811
    oid = await short_order(p5)
    await second(p5, [("CF0010", 2)])                                    # a part: applied
    await second(p5, [("CF0010", 4)], "third.edi", "000000782", "9301b")  # more: held
    assert await applied(p5) == [True, True, False] and await status(p5.edi, oid) == F.ON_HOLD
    async with p5.factory(OP) as u:
        await acting.release_hold(u, p5.edi.env.tasks, oid, "Shipped", "the first two stand")
        await u.commit()
    assert await status(p5.edi, oid) == F.SHIPPED and await lines(p5, oid) == {"CF0092": 12, "CF0010": 2}
    await no_write(p5)  # the second is "Not needed" and stays so


async def test_after_keep_the_pass_adds_a_part_beside_the_kept_one_and_a_press_for_a_third_is_refused(p5):  # noqa: F811
    oid = await short_order(p5)
    await second(p5, [("CF0010", 5)])                                    # more: held
    async with p5.factory(OP) as u:
        await acting.release_hold(u, p5.edi.env.tasks, oid, "Shipped", "the first stands")
        await u.commit()
    r = await second(p5, [("CF0010", 5)], "again.edi", "000000782")       # the kept one sent again: decided already
    assert r.repeats == 1 and await status(p5.edi, oid) == F.SHIPPED
    r = await second(p5, [("CF0010", 3)], "third.edi", "000000783", "9301b")
    assert r.parts == 1 and await applied(p5) == [True, False, False, True] and (await lines(p5, oid))["CF0010"] == 3
    await second(p5, [("CF0010", 2)], "fourth.edi", "000000784", "9301c")  # more again: held, and the kept one counts as waiting
    d = await read(p5, oid)
    assert (d.later_945.shipment_id, d.later_945.kind) == (5, None)
    assert "second.edi" in d.later_945.refused and "again.edi" not in d.later_945.refused and "Keep the applied 945" in d.later_945.refused
    for a in ("apply_part_945", "apply_more_945"):
        assert (await act(p5, a, oid, 5)).status == "skipped"
    async with p5.factory(OP) as u:  # what it says to do works
        await acting.release_hold(u, p5.edi.env.tasks, oid, "Shipped", "asked the warehouse")
        await u.commit()
    assert await status(p5.edi, oid) == F.SHIPPED and (await lines(p5, oid))["CF0010"] == 3
    await no_write(p5)


async def test_a_press_sends_the_first_945s_write_its_hold_stopped_with_its_own_quantities(p5):  # noqa: F811
    """Not fulfilled at the 997, and the hold came before the first 945's write ran: the press lets that one go, for what the first
    945 shipped and with its own tracking — never the later part's."""
    await live_store(p5)
    await a_paid_order(p5.edi, lines=LINES)
    p5.shop.fulfillment_orders_by_order["1"] = [{"id": 501, "status": "open", "line_items": [
        {"id": 601, "line_item_id": 11, "fulfillable_quantity": 2}, {"id": 602, "line_item_id": 12, "fulfillable_quantity": 4}]}]
    await p5.edi.out.queue_due()
    await p5.edi.worker.run_once()
    await store_945(p5, x945("9301", ["LX*1", *row("CF0092", 12), *row("CF0010", 1)]), "first.edi")
    p5.edi.clock.now = T0 + timedelta(days=5)
    await p5.ships.process_945s()                       # its write waits in the outbox
    [o] = await p5.q("SELECT id FROM sales_orders")
    await second(p5, [("CF0010", 4)])                   # held: 5 of 4
    await p5.worker.run_once()                          # the order is On Hold: suppressed
    assert writes(p5) == []
    assert (await act(p5, "apply_more_945", o["id"], 2)).status == "done"
    await p5.worker.run_once()
    [(_, call)] = [c for c in p5.shop.calls if c[0] == "create_fulfillment"]
    assert call["items"] == [{"fulfillment_order_id": 501, "fulfillment_order_line_items": [{"id": 601, "quantity": 2}, {"id": 602, "quantity": 1}]}]
    assert call["tracking"]["number"] == "1Z-TRACK" and writes(p5).count("create_fulfillment") == 1


# ── two at once ──────────────────────────────────────────────────────────────

async def meets(t, oid, coming, meanwhile):
    async with t.factory(OP) as u:
        await u.session.get(Order, oid, with_for_update=True)
        task = asyncio.create_task(coming())
        await asyncio.sleep(0.5)
        assert not task.done()  # it waits for the order
        await meanwhile(u)
        await u.commit()
    return await asyncio.wait_for(task, 10)


async def test_two_presses_at_once_one_counts(p5):  # noqa: F811
    oid = await held_part(p5)
    [a, b] = await asyncio.gather(act(p5, "apply_part_945", oid, 2, reason="one"), act(p5, "apply_part_945", oid, 2, reason="two"))
    assert sorted([a.status, b.status]) == ["done", "skipped"]
    assert await lines(p5, oid) == {"CF0092": 12, "CF0010": 4}


async def test_a_return_recorded_while_the_press_waits_refuses_it(p5):  # noqa: F811
    from app.models.invoices import OrderReturn
    oid = await held_part(p5)
    lid = await _line(p5, oid, "CF0010")

    async def back(u):
        u.session.add(OrderReturn(order_id=oid, kind="Returned", reason="came back", lines=[{"order_line_id": lid, "qty_ea": 1}]))
    r = await meets(p5, oid, lambda: act(p5, "apply_part_945", oid, 2), back)
    assert r.status == "skipped" and "refund or a return" in r.reason and await applied(p5) == [True, False]


async def test_the_pass_waits_for_the_order_and_reads_it_again(p5):  # noqa: F811
    oid = await short_order(p5)
    await store_945(p5, x945("9301a", ["LX*1", *row("CF0010", 4)], header=HEAD, ctl="000000781"), "second.edi")

    async def child(u):
        (await u.session.get(Order, oid)).invoice_status = "Blocked"
    r = await meets(p5, oid, p5.ships.process_945s, child)
    assert (r.failed, r.parts, r.second) == (0, 0, 1) and await applied(p5) == [True, False]


async def test_a_press_waits_for_the_pass_that_brings_another(p5):  # noqa: F811
    oid = await held_part(p5)
    await store_945(p5, x945("9301b", ["LX*1", *row("CF0010", 3)], header=HEAD, ctl="000000782"), "third.edi")
    [press, r] = await asyncio.gather(act(p5, "apply_part_945", oid, 2), p5.ships.process_945s())
    assert r.failed == 0 and (await lines(p5, oid))["CF0010"] in (0, 4)  # one of the two, never both
    n = (await applied(p5)).count(True)
    assert n == (2 if press.status == "done" else 1)


# ── the stock ────────────────────────────────────────────────────────────────

async def test_each_part_takes_its_stock_out_and_is_delivered_once(p5):  # noqa: F811
    async def run():
        async with p5.factory(SYSTEM_ACTOR) as u:
            out = await sync(u)
            await u.commit()
        async with p5.factory(OP) as u:
            page = await StockItemService(u).list(QuerySpec(page_size=100))
        return out, {i.item_code: (i.shipped_ea, i.to_customer_ea) for i in page.items}
    await short_order(p5, first=(("CF0092", 6),))
    out, items = await run()
    assert out == {"shipment_row": 1, "delivery": 1} and items["CF0092"] == (6, 0)
    await second(p5, [("CF0092", 4, "L2")])
    out, items = await run()
    assert out == {"shipment_row": 1, "delivery": 1} and items["CF0092"] == (10, 0)  # the line's delivery was in the ledger already
    await second(p5, [("CF0092", 2, "L3"), ("CF0010", 4, "L4")], "third.edi", "000000782", "9301b")
    out, items = await run()
    assert out == {"shipment_row": 2, "delivery": 2} and (items["CF0092"], items["CF0010"]) == ((12, 0), (4, 0))
    assert (await run())[0] == {}
    assert await p5.q("SELECT count(*) n, count(DISTINCT source_key) k FROM sales_stock_moves WHERE source_type = 'delivery'") == [{"n": 4, "k": 4}]


# ── the marks ────────────────────────────────────────────────────────────────

async def listed(t, *filters):
    async with t.factory(OP) as u:
        page = await OrderService(u).list(QuerySpec(filters=tuple(Filter(field=f, op=op, value=v) for f, op, v in filters)))
    return [o.source_order_number for o in page.items]


async def test_the_marks_and_their_filters(p5):  # noqa: F811
    oid = await short_order(p5)
    two, over = ("applied_945s", "gte", 2), ("over_shipped_ea", "gt", 0)
    assert (await listed(p5, two), await listed(p5, over), await listed(p5, ("applied_945s", "eq", 1))) == ([], [], ["9301"])
    await second(p5, [("CF0092", 1)])  # held: more than ordered, so not counted
    assert (await listed(p5, two), await listed(p5, over)) == ([], [])
    await act(p5, "apply_more_945", oid, 2)
    assert (await listed(p5, two), await listed(p5, over)) == (["9301"], ["9301"])
    await second(p5, [("CF0092", 1)], "again.edi", "000000782")  # a resend: not counted
    assert (await read(p5, oid)).applied_945s == 2 and await listed(p5, ("applied_945s", "gte", 3)) == []
    await second(p5, [("CF0010", 4)], "third.edi", "000000783", "9301b")
    d = await read(p5, oid)
    assert (d.applied_945s, d.over_shipped_ea, d.short_shipped) == (3, 1, False)
    async with p5.factory(OP) as u:
        [o] = (await OrderService(u).list(QuerySpec())).items
    assert (o.applied_945s, o.over_shipped_ea) == (3, 1)  # the list's row says the same


async def test_over_shipped_is_above_the_940_not_above_the_line_as_it_is_now(p5):  # noqa: F811
    oid = await short_order(p5, first=(("CF0092", 14), ("CF0010", 4)))
    assert (await read(p5, oid)).over_shipped_ea == 2
    async with p5.factory(SYSTEM_ACTOR) as u:  # the source changed after the 940: what it carried stays 12
        await u.session.execute(sa.text("UPDATE sales_order_lines SET qty_ea = 20 WHERE item_code = 'CF0092'"))
        await u.commit()
    assert (await read(p5, oid)).over_shipped_ea == 2
