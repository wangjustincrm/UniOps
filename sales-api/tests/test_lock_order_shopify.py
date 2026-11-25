"""One order of locks for a shipment (L2, review of stage 3, 2026-11-24): its order, then the shipment, then its rows — the Shopify
write, its outcome after the call, a 945 row's decision and the release of a hold. Two real sessions each time."""
import asyncio

import pytest
import sqlalchemy as sa

from app.models.orders import Order
from app.models.shipments import Shipment, ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.services.order_holds import OrderActions
from app.services.shipments import FULFILL
from tests.sending import put_row, rows, says
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import live_store, p5, shipped_order, writes, x945  # noqa: F401 — p5 is a fixture


async def shipment(t, rows_=("LX*1", "W12*CP*12*12*0*EA**VN*CF0092")):
    """An order shipped by its 945, its Shopify write queued: (order id, shipment id)."""
    await live_store(t)
    o, _, _ = await shipped_order(t, x945("9301", list(rows_)))
    [sh] = await t.q("SELECT id FROM sales_shipments")
    return o.id, sh["id"]


async def another_order(t, oid) -> int:
    """A copy of the order, to which the shipment can be given."""
    async with t.factory(SYSTEM_ACTOR) as u:
        o = await u.session.get(Order, oid)
        new = Order(partner_code=o.partner_code, source_type="SHOPIFY", source_order_id="other", source_order_number="other", connection_id=o.connection_id,
                    fulfillment_status=o.fulfillment_status, invoice_status=o.invoice_status, first_received_at=o.first_received_at, version=1)
        u.session.add(new)
        await u.session.flush()
        id = new.id
        await u.commit()
        return id


async def meets(t, oid, coming, meanwhile, *also):
    """A person holds the order; `coming` must wait for it holding neither the shipment nor its rows; the person goes on to
    them (`meanwhile`) and commits; `coming` then ends. In the other order the two wait for each other."""
    async with t.factory(OP) as u:
        s = u.session
        await s.get(Order, oid, with_for_update=True)
        task = asyncio.create_task(coming())
        await asyncio.sleep(0.5)
        held = [m.__tablename__ for m, id in also
                if (await s.execute(sa.select(m.id).where(m.id == id).with_for_update(skip_locked=True))).scalar() is None]
        if task.done() or held:
            await u.rollback()
            await asyncio.wait({task}, timeout=5)
            pytest.fail(f"it did not wait for the order, or held {held} meanwhile")
        await meanwhile(u)
        await u.commit()
    return await asyncio.wait_for(task, 10)


async def lock_its_shipments(u, oid, t):
    await OrderActions(u, t.edi.env.tasks)._applied(oid)  # as releasing a hold does


def give(sid, other):
    async def go(u):
        (await u.session.get(Shipment, sid, with_for_update=True)).order_id = other
        await u.session.flush()
    return go


# ── the write's claim ────────────────────────────────────────────────────────

async def test_the_claim_of_a_shopify_write_and_a_release(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    outcome, claim = await meets(p5, oid, p5.worker._claim, lambda u: lock_its_shipments(u, oid, p5), (Shipment, sid))
    assert outcome is None and claim.payload == {"shipment_id": sid}  # it goes on


async def test_a_shipment_given_another_order_while_its_claim_waits_writes_nothing(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    other = await another_order(p5, oid)
    out = await meets(p5, oid, p5.worker._claim, give(sid, other), (Shipment, sid))
    assert out == ("cancelled", None) and writes(p5) == []
    [r] = await rows(p5, FULFILL)
    assert (r["status"], r["last_error"]) == ("CANCELLED", "the shipment or its order is gone")


async def test_a_shipment_without_an_order_or_gone_writes_nothing(p5):  # noqa: F811
    _, sid = await shipment(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        assert await p5.ships._pre_check({"shipment_id": 99_999}, u) == "the shipment or its order is gone"
        (await u.session.get(Shipment, sid)).order_id = None
        await u.session.flush()
        assert await p5.ships._pre_check({"shipment_id": sid}, u) == "the shipment or its order is gone"


async def test_the_write_itself_is_as_before(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    assert (await p5.worker.run_once()).done == 2 and writes(p5) == ["create_fulfillment", "close_order"]
    [x] = await p5.q("SELECT s.shopify_status, s.shopify_fulfillment_id a, o.shopify_fulfillment_id b FROM sales_shipments s JOIN sales_orders o ON o.id = s.order_id")
    assert x["shopify_status"] == "Done" and x["a"] == x["b"] is not None


# ── its outcome: the fulfilment exists, its id is never lost ─────────────────

async def ids(t, sid, *orders):
    sh = (await t.q("SELECT shopify_fulfillment_id f FROM sales_shipments WHERE id = :i", i=sid))[0]["f"]
    return (sh, *[(await t.q("SELECT shopify_fulfillment_id f FROM sales_orders WHERE id = :i", i=o))[0]["f"] for o in orders])


async def test_the_outcome_and_a_release(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    await meets(p5, oid, lambda: p5.ships._record(sid, "777", "fulfillment created"), lambda u: lock_its_shipments(u, oid, p5), (Shipment, sid))
    assert await ids(p5, sid, oid) == ("777", "777")


async def test_the_outcome_of_a_shipment_given_another_order_meanwhile_is_kept_on_the_order_it_has_now(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    other = await another_order(p5, oid)
    await meets(p5, oid, lambda: p5.ships._record(sid, "777", "fulfillment created"), give(sid, other), (Shipment, sid))
    assert await ids(p5, sid, oid, other) == ("777", None, "777")
    [log] = await p5.q("SELECT order_id FROM sales_log WHERE message LIKE 'Shopify: fulfillment created%'")
    assert log["order_id"] == other


async def test_the_outcome_of_a_shipment_without_an_order_is_kept_on_it(p5):  # noqa: F811
    oid, sid = await shipment(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Shipment, sid)).order_id = None
        await u.commit()
    await asyncio.wait_for(p5.ships._record(sid, "777", "fulfillment created"), 5)
    assert await ids(p5, sid, oid) == ("777", None)


# ── a 945 row's decision ─────────────────────────────────────────────────────

async def to_decide(t):
    """A 945 with a row that matches no line of its order: (order, shipment, row)."""
    oid, sid = await shipment(t, ("LX*1", "W12*CP*12*12*0*EA**VN*CF0092", "W12*CP*1*1*0*EA**VN*CF0010"))
    [r] = await t.q("SELECT id FROM sales_shipment_rows WHERE needs_review")
    return oid, sid, r["id"]


def decide(t, sid, rid):
    async def go():
        async with t.factory(OP) as u:
            await OrderActions(u, t.edi.env.tasks).resolve_row(sid, rid, None, "a sample, accepted")
            await u.commit()
    return go


async def test_a_row_s_decision_and_a_release(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)
    await meets(p5, oid, decide(p5, sid, rid), lambda u: lock_its_shipments(u, oid, p5), (Shipment, sid), (ShipmentRow, rid))
    assert await p5.q("SELECT 1 FROM sales_shipment_rows WHERE needs_review") == []


async def test_a_row_s_decision_on_a_shipment_given_another_order_meanwhile_is_refused(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)
    other = await another_order(p5, oid)
    e = await meets(p5, oid, lambda: says(decide(p5, sid, rid)), give(sid, other), (Shipment, sid), (ShipmentRow, rid))
    assert (e.code, e.status_code) == ("entity.not_found", 404)
    assert len(await p5.q("SELECT 1 FROM sales_shipment_rows WHERE needs_review")) == 1


async def test_a_row_s_other_refusals_stand(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)
    for s, r in ((sid, rid + 999), (sid + 999, rid)):
        assert (await says(decide(p5, s, r))).code == "entity.not_found"
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, oid)).shadow = True
        await u.commit()
    assert (await says(decide(p5, sid, rid))).code == "order.shadow"
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, oid)).shadow = False
        await u.commit()
    assert await says(decide(p5, sid, rid)) is None
    e = await says(decide(p5, sid, rid))
    assert isinstance(e, BusinessError) and e.code == "shipment.row_resolved"


async def test_a_second_decision_at_the_same_moment_waits_and_is_refused(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)

    async def first(u):
        await OrderActions(u, p5.edi.env.tasks).resolve_row(sid, rid, None, "a sample, accepted")
    e = await meets(p5, oid, lambda: says(decide(p5, sid, rid)), first, (Shipment, sid), (ShipmentRow, rid))
    assert e.code == "shipment.row_resolved"


async def test_a_retry_locks_only_its_shipment_and_is_as_before(p5):  # noqa: F811
    from app.services.order_views import OrderService
    oid, sid = await shipment(p5)
    await p5.worker.run_once()
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Shipment, sid)).shopify_status = "Failed"
        await u.commit()
    await put_row(p5, FULFILL, {"shipment_id": sid}, ("sales_shipments", sid), "FAILED")
    async with p5.factory(OP) as held, p5.factory(OP) as u:
        await held.session.get(Order, oid, with_for_update=True)
        await asyncio.wait_for(OrderService(u).retry_shopify(sid, p5.ships), 5)
