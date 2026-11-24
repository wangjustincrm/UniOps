"""The order's record actions (stage 3 b): the same rule and work as the single routes; one at a time where it is a decision
about one order; a required reason; a person who saw an old page is told so. None sends a file or an email."""
import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.shadow import TEST_RUN_REFUSAL
from app.models.orders import Order
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import Stale
from app.platform.errors import BusinessError, InvalidInput
from app.schemas.invoices import ReleaseIn, ReturnIn
from app.services.invoice_views import InvoiceService
from app.services.order_actions import ORDER_ACTIONS
from tests.test_invoicing import OP, confirm, order_version, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_order_amend import _order
from tests.test_orders import env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

READER = SimpleNamespace(**{**vars(OP), "name": "Reader"}) if hasattr(OP, "__dict__") else OP


async def act(t, id, oid, rt=None, version=None, **kw):
    async with t.factory(OP) as u:
        v = version if version is not None else await order_version(u, oid)
        out = await run_action(u, ORDER_ACTIONS, id, ActionIn(ids={oid: v}, **kw), rt or SimpleNamespace(tasks=t.edi.env.tasks))
        await u.commit()
    return out.results[0]


async def status(t, oid):
    return (await t.q("SELECT fulfillment_status s FROM sales_orders WHERE id = :o", o=oid))[0]["s"]


async def patch(t, oid, **values):
    async with t.factory(SYSTEM_ACTOR) as u:
        o = await u.session.get(Order, oid)
        for k, v in values.items():
            setattr(o, k, v)
        await u.commit()


async def test_cancel_is_one_at_a_time_with_why_and_sends_nothing(p6):  # noqa: F811
    o = await _order(p6, "PO-OA1")
    with pytest.raises(BusinessError) as e:
        await act(p6, "cancel", o.id)
    assert (e.value.code, e.value.status_code) == ("reason.required", 422)
    with pytest.raises(InvalidInput) as e:
        async with p6.factory(OP) as u:
            await run_action(u, ORDER_ACTIONS, "cancel", ActionIn(ids={o.id: 1, o.id + 1: 1}, reason="both"))
    assert e.value.code == "action.single"
    r = await act(p6, "cancel", o.id, reason="customer withdrew")
    assert r.status == "done" and await status(p6, o.id) == "Cancelled"
    r = await act(p6, "cancel", o.id, reason="again")
    assert r.status == "skipped" and "cannot be cancelled here" in r.reason
    assert await p6.q("SELECT 1 FROM sales_outbox") == []
    [row] = await p6.q("SELECT reason FROM sales_audit WHERE entity = 'sales_orders' AND entity_id = :o AND reason LIKE 'order cancelled%'", o=str(o.id))
    assert row["reason"] == "order cancelled: customer withdrew"


async def test_two_people_on_one_order_the_second_is_told(p6):  # noqa: F811
    o = await _order(p6, "PO-OA2")
    async with p6.factory(OP) as a, p6.factory(OP) as b:
        v = await order_version(a, o.id)
        rt = SimpleNamespace(tasks=p6.edi.env.tasks)
        await run_action(a, ORDER_ACTIONS, "cancel", ActionIn(ids={o.id: v}, reason="first"), rt)
        second = asyncio.create_task(run_action(b, ORDER_ACTIONS, "cancel", ActionIn(ids={o.id: v}, reason="second"), rt))
        await asyncio.sleep(0.3)
        assert not second.done()  # waits for the row
        await a.commit()
        [r] = (await second).results
        await b.rollback()
    assert r.status == "skipped" and "cannot be cancelled" in r.reason  # already done is said as that, not as stale
    o2 = await _order(p6, "PO-OA3")
    r = await act(p6, "cancel", o2.id, version=99, reason="old page")
    assert (r.status, r.code) == ("failed", "entity.stale") and await status(p6, o2.id) == "Pending"


async def test_a_shadow_order_is_refused_by_every_action_that_changes_it(p6):  # noqa: F811
    o = await _order(p6, "PO-OA4")
    await patch(p6, o.id, shadow=True)
    for id, kw in (("cancel", {"reason": "why not"}), ("release_hold", {"reason": "why not", "input": {"to": "Cancelled"}})):
        r = await act(p6, id, o.id, **kw)
        assert (r.status, r.reason) == ("skipped", TEST_RUN_REFUSAL), (id, r)


async def test_release_hold_goes_where_it_may_and_queues_the_shopify_write_once(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-OA5")
    await patch(p6, o.id, source_type="SHOPIFY", fulfillment_status="On Hold", fulfillment_status_before_hold="Shipped")
    async with p6.factory(SYSTEM_ACTOR) as u:
        sh = (await u.session.execute(sa.select(Shipment).where(Shipment.order_id == o.id, Shipment.applied.is_(True)))).scalars().first()
        sh.shopify_status = "Not needed"
        await u.commit()
    r = await act(p6, "release_hold", o.id, reason="first 945 stands", input={"to": "Pending"})
    assert (r.status, r.code) == ("failed", "order.hold_exit")
    r = await act(p6, "release_hold", o.id, reason="first 945 stands", input={"to": "Shipped"})
    assert r.status == "done", r
    assert await status(p6, o.id) == "Shipped"
    r = await act(p6, "release_hold", o.id, reason="again", input={"to": "Shipped"})
    assert r.status == "skipped" and "not on hold" in r.reason
    rows = await p6.q("SELECT kind, status FROM sales_outbox WHERE kind LIKE 'shopify%'")
    assert len(rows) == 1 and rows[0]["status"] == "PENDING"  # queued once; nothing is sent here


async def test_recheck_in_bulk_reads_the_store_before_any_lock_and_says_what_is_left(env):  # noqa: F811
    env.shop.orders = [shopify_order(1, 9501, lines=[{"id": 11, "sku": "NOPE", "title": "x", "quantity": 1, "price": "1.00", "requires_shipping": True, "variant_id": 77}]),
                       shopify_order(2, 9502)]
    await env.pull()
    ids = {r["id"]: r["version"] for r in await env.q("SELECT id, version FROM sales_orders ORDER BY id")}
    states = {r["id"]: r["fulfillment_status"] for r in await env.q("SELECT id, fulfillment_status FROM sales_orders")}
    review = [i for i, s in states.items() if s == "Review"]
    assert len(review) == 1, states
    locked = []

    class Store:
        async def variant(self, conn, variant_id):  # noqa: ARG002
            async with env.factory(SYSTEM_ACTOR) as other:  # another unit can still lock every order
                got = (await other.session.execute(sa.text("SELECT id FROM sales_orders FOR UPDATE SKIP LOCKED"))).all()
                locked.append(len(got))
            return {"barcode": None}
    rt = SimpleNamespace(tasks=env.tasks, intake=SimpleNamespace(tasks=env.tasks), container=SimpleNamespace(shopify=Store()))
    async with env.factory(OP) as u:
        out = await run_action(u, ORDER_ACTIONS, "recheck", ActionIn(ids=ids), rt)
        await u.commit()
    by = {r.id: r for r in out.results}
    assert by[review[0]].status == "done" and by[review[0]].note.startswith("still in review:")
    assert all(r.status == "skipped" and r.reason == "not in review" for i, r in by.items() if i != review[0])
    assert locked and set(locked) == {len(ids)}


async def test_drafts_submit_and_cancel_as_their_routes(env):  # noqa: F811
    from app.schemas.orders import AddressIn, ManualOrderIn
    from app.services.order_views import ManualOrderService
    t = SimpleNamespace(factory=env.factory, edi=SimpleNamespace(env=env), q=env.q)
    async with env.factory(SYSTEM_ACTOR) as u:
        from app.models.config import Partner
        if (await u.session.execute(sa.select(Partner).where(Partner.code == "MAN"))).scalar_one_or_none() is None:
            u.session.add(Partner(code="MAN", name="Manual", abbrev="MAN", channel="MANUAL", settlement_mode="INVOICE", sending_mode="AUTOMATIC",
                                  delay_minutes=0, invoice_method="NONE", dc_routing_required=False, version=1))
        await u.commit()
    made = []
    for _ in range(2):
        async with env.factory(OP) as u:
            d = await ManualOrderService(u).create(ManualOrderIn(reason="Sample", partner_code="MAN", customer_reference="x", carrier="Purolator", ship_to=AddressIn(
                name="N", address1="1 rue", city="Montréal", province="QC", zip="H2X 1Y4", country="CA", phone="514-555-0100"), lines=[{"item_code": "NOPE", "quantity": 1, "unit": "CASE"}]))
            await u.commit()
            made.append(d.id)
    r = await act(t, "submit_draft", made[0])
    assert (r.status, r.code) == ("failed", "order.lines_invalid")  # the same rule as the route
    r = await act(t, "cancel_draft", made[1])
    assert r.status == "done" and await status(t, made[1]) == "Cancelled"
    r = await act(t, "submit_draft", made[1])
    assert r.status == "skipped" and "not a draft" in r.reason
    env.shop.orders = [shopify_order(1, 9503)]
    await env.pull()
    [o] = await env.q("SELECT id FROM sales_orders WHERE source_type = 'SHOPIFY'")
    r = await act(t, "cancel_draft", o["id"])
    assert r.status == "skipped" and r.reason == "not a manual order"


@pytest.mark.parametrize("id", ["recheck", "cancel", "release_hold", "submit_draft", "cancel_draft"])
async def test_each_needs_the_operators_permission(p6, monkeypatch, id):  # noqa: F811
    o = await _order(p6, "PO-OA6")

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(p6.factory, "permissions", reader)
    async with p6.factory(OP) as u:
        with pytest.raises(PermissionDenied):
            await run_action(u, ORDER_ACTIONS, id, ActionIn(ids={o.id: 1}, reason="why not", input={"to": "Cancelled"}))
    assert await status(p6, o.id) == "Pending"


async def test_the_same_return_twice_and_a_release_from_an_old_page_are_refused(p6):  # noqa: F811
    """M5 and U3: the order moves with a return, so the same page cannot record it again."""
    o = await shipped_850(p6, "PO-OA7")
    await p6.inv.advance()
    await confirm(p6, o.id)
    [line] = await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :o", o=o.id)
    async with p6.factory(OP) as u:
        seen = await order_version(u, o.id)
    body = ReturnIn(order_id=o.id, version=seen, kind="Returned", reason="damaged", lines=[{"order_line_id": line["id"], "qty_ea": 6}])
    async with p6.factory(OP) as a, p6.factory(OP) as b:
        await InvoiceService(a).record_return(p6.inv, body)
        second = asyncio.create_task(InvoiceService(b).record_return(p6.inv, body))
        await asyncio.sleep(0.3)
        assert not second.done()
        await a.commit()
        with pytest.raises(Stale):
            await second
        await b.rollback()
    assert (await p6.q("SELECT count(*) n FROM sales_order_returns"))[0]["n"] == 1
    async with p6.factory(OP) as u:
        for v, error in ((None, InvalidInput), (seen, Stale)):
            with pytest.raises(error):
                await InvoiceService(u).release(p6.inv, ReleaseIn(order_id=o.id, version=v, reason="decided"))
        [i] = (await u.session.execute(sa.text("SELECT id FROM sales_invoices WHERE order_id = :o"), {"o": o.id})).scalars()
        d = await InvoiceService(u).detail(i)
        assert d.order_version == await order_version(u, o.id)  # what the invoice's page sends
        assert (await InvoiceService(u).release(p6.inv, ReleaseIn(order_id=o.id, version=d.order_version, reason="decided")))["invoice_status"] == "Ready"
        await u.commit()
