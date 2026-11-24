"""Fulfil in Shopify now and a shipment's retry (stage 3 g): once, by the version seen, and never past a write still under
way. The store is the in-memory fake behind the real guard."""
from types import SimpleNamespace

import pytest

from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import Stale
from app.platform.errors import BusinessError, InvalidInput
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from app.services.outbound import FULFILL_AT_997
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.shipments import CLOSE, CLOSE_AT_997, FULFILL
from tests.sending import STATE_IDS, STATES, crash, put_row, rows, said, says, set_row, twice, way_out
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import ack997, sent_order, store_997
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import live_store, p5, shipped_order, writes, x945  # noqa: F401 — p5 is a fixture

KINDS = (FULFILL_AT_997, CLOSE_AT_997)


async def accepted(t, *, live=True):
    """An order its 940 accepted, whose write at the 997 was skipped (the store's Write was off then)."""
    o, d = await sent_order(t.edi)
    await store_997(t.edi, ack997(d["st02"]))
    await t.edi.out.process_997s()
    await t.worker.run_once()
    if live:
        await live_store(t)
    return o


async def version(t, oid) -> int:
    return (await t.q("SELECT version FROM sales_orders WHERE id = :i", i=oid))[0]["version"]


def press(t, oid, v):
    async def go():
        async with t.factory(OP) as u:
            await OrderService(u).fulfil_at_997(oid, t.ships, v)
            await u.commit()
    return go


async def act(t, oid, v):
    async with t.factory(OP) as u:
        out = await run_action(u, ORDER_ACTIONS, "fulfil_in_shopify", ActionIn(ids={oid: v}), SimpleNamespace(shipments=t.ships))
        await u.commit()
    return out.results[0]


async def queued(t):
    return [r for r in await rows(t, *KINDS) if r["status"] == "PENDING"]


@pytest.mark.parametrize(("status", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_fulfil_and_a_write_in_each_state(p5, status, minutes, expected):  # noqa: F811
    o = await accepted(p5)
    for kind in KINDS:
        id = await put_row(p5, kind, {"order_id": o.id}, ("sales_orders", o.id), status, minutes)
        e = await says(press(p5, o.id, await version(p5, o.id)))
        assert said(e) == expected, kind
        if expected is None:  # through: its own new row
            [r] = [r for r in await queued(p5)]
            await set_row(p5, r["id"], status="DONE")
        elif expected[1] == "unknown":
            assert way_out(e)
        await set_row(p5, id, status="DONE")


async def test_another_order_s_and_another_kind_s_row_do_not_block(p5):  # noqa: F811
    o = await accepted(p5)
    await put_row(p5, FULFILL_AT_997, {"order_id": o.id + 1}, ("sales_orders", o.id + 1), "PENDING")
    await put_row(p5, "send_940", {"order_id": o.id}, ("sales_orders", o.id), "IN_FLIGHT", 10)
    await put_row(p5, FULFILL, {"shipment_id": o.id}, ("sales_shipments", o.id), "PENDING")
    assert await says(press(p5, o.id, await version(p5, o.id))) is None


async def test_an_outcome_never_recorded_is_closed_by_the_next_pass_and_then_does_not_block(p5):  # noqa: F811
    o = await accepted(p5)
    id = await put_row(p5, FULFILL_AT_997, {"order_id": o.id}, ("sales_orders", o.id), "IN_FLIGHT", -10)
    assert said(await says(press(p5, o.id, await version(p5, o.id)))) == ("send.outcome_unknown", "unknown")
    assert (await p5.worker.run_once()).failed == 1 and writes(p5) == []  # closed, never run again
    assert [(r["id"], r["status"]) for r in await rows(p5, FULFILL_AT_997) if r["status"] == "FAILED"] == [(id, "FAILED")]
    assert await says(press(p5, o.id, await version(p5, o.id))) is None
    await p5.worker.run_once()
    assert writes(p5) == ["create_fulfillment", "close_order"]


async def test_two_presses_at_once_write_once(p5):  # noqa: F811
    o = await accepted(p5)
    v = await version(p5, o.id)
    e = await twice(p5, OP, lambda u: OrderService(u).fulfil_at_997(o.id, p5.ships, v))
    assert said(e) == ("send.queued", "pending") and len(await queued(p5)) == 1
    await p5.worker.run_once()
    assert writes(p5) == ["create_fulfillment", "close_order"]


async def test_a_press_from_a_page_opened_before_is_refused_even_once_the_write_is_done(p5):  # noqa: F811
    o = await accepted(p5)
    seen = await version(p5, o.id)
    assert await says(press(p5, o.id, seen)) is None
    await p5.worker.run_once()
    assert isinstance(await says(press(p5, o.id, seen)), Stale)
    assert isinstance(await says(press(p5, o.id, None)), InvalidInput)
    assert writes(p5) == ["create_fulfillment", "close_order"]


async def test_the_press_alone_moves_the_order_so_a_second_page_looks_again(p5):  # noqa: F811
    o = await accepted(p5)
    seen = await version(p5, o.id)
    assert await says(press(p5, o.id, seen)) is None
    [r] = await queued(p5)
    await set_row(p5, r["id"], status="CANCELLED")  # nothing under way any more, nothing written
    assert isinstance(await says(press(p5, o.id, seen)), Stale)


async def test_who_pressed_is_in_the_order_s_activity(p5):  # noqa: F811
    o = await accepted(p5)
    assert await says(press(p5, o.id, await version(p5, o.id))) is None
    await says(press(p5, o.id, await version(p5, o.id)))  # refused (queued): not a second one
    async with p5.factory(OP) as u:
        timeline = (await OrderService(u).detail(o.id)).timeline
    asked = [e for e in timeline if e.action == "fulfil_in_shopify"]
    assert [(e.user_name, e.reason, e.before, e.after) for e in asked] == [(OP.name, "Shopify: fulfil at the 997", None, None)]
    assert await p5.q("SELECT 1 FROM sales_log WHERE message LIKE 'Fulfil in Shopify now%'")  # the log line stays


async def test_who_retried_a_shipment_is_in_its_audit(p5):  # noqa: F811
    sid = await failed_shipment(p5)
    assert await says(retry(p5, sid)) is None
    [a] = await p5.q("SELECT user_name, reason, after FROM sales_audit WHERE entity = 'sales_shipments' AND entity_id = :i AND user_name = :n", i=str(sid), n=OP.name)
    assert (a["reason"], a["after"]["shopify_status"]) == ("Shopify write-back: retry", "Pending")


async def test_a_failed_write_can_be_asked_again(p5):  # noqa: F811
    o = await accepted(p5)
    p5.shop.fail["create_fulfillment"] = RuntimeError("connection reset")
    assert await says(press(p5, o.id, await version(p5, o.id))) is None
    assert (await p5.worker.run_once()).failed == 1
    del p5.shop.fail["create_fulfillment"]
    assert await says(press(p5, o.id, await version(p5, o.id))) is None
    await p5.worker.run_once()
    assert writes(p5) == ["create_fulfillment", "create_fulfillment", "close_order"]


async def test_each_refusal_stands_and_queues_nothing(p5, monkeypatch):  # noqa: F811
    o = await accepted(p5)
    before = await rows(p5, *KINDS)

    async def with_order(**values):
        async with p5.factory(SYSTEM_ACTOR) as u:
            row = await u.session.get(Order, o.id)
            old = {k: getattr(row, k) for k in values}
            for k, v in values.items():
                setattr(row, k, v)
            await u.commit()
        return old

    for values, code in (({"source_type": "MANUAL"}, "order.not_shopify"), ({"fulfillment_status": F.SHIPPED.value}, "order.not_accepted"),
                         ({"fulfillment_status": F.CANCELLED.value}, "order.not_accepted"), ({"shadow": True}, "order.shadow")):
        old = await with_order(**values)
        assert (await says(press(p5, o.id, await version(p5, o.id)))).code == code
        r = await act(p5, o.id, await version(p5, o.id))
        assert r.status == "skipped" and r.reason
        await with_order(**old)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    assert (await says(press(p5, o.id, await version(p5, o.id)))).code == "order.shadow"
    async with p5.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        await u.commit()
    was = p5.factory.permissions

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(p5.factory, "permissions", reader)
    assert isinstance(await says(press(p5, o.id, 1)), PermissionDenied)
    with pytest.raises(PermissionDenied):
        await act(p5, o.id, 1)
    monkeypatch.setattr(p5.factory, "permissions", was)
    assert await rows(p5, *KINDS) == before and writes(p5) == []
    r = await act(p5, o.id, await version(p5, o.id))  # and it is admitted
    assert r.status == "done" and len(await queued(p5)) == 1


async def test_the_write_off_again_is_stopped_at_the_second_barrier(p5):  # noqa: F811
    o = await accepted(p5, live=False)
    assert await says(press(p5, o.id, await version(p5, o.id))) is None
    assert (await p5.worker.run_once()).cancelled == 1 and writes(p5) == []


async def test_as_a_record_action_it_says_queued_or_changed_meanwhile(p5):  # noqa: F811
    o = await accepted(p5)
    v = await version(p5, o.id)
    r = await act(p5, o.id, v + 5)
    assert (r.status, r.code) == ("failed", "entity.stale")
    assert (await act(p5, o.id, v)).status == "done"
    r = await act(p5, o.id, v)
    assert r.status == "skipped" and "already queued" in r.reason  # what is under way, whatever the version
    assert len(await queued(p5)) == 1


# ── a shipment's retry ───────────────────────────────────────────────────────

def retry(t, sid):
    async def go():
        async with t.factory(OP) as u:
            await OrderService(u).retry_shopify(sid, t.ships)
            await u.commit()
    return go


async def failed_shipment(t) -> int:
    await live_store(t)
    t.shop.fail["create_fulfillment"] = RuntimeError("connection reset")
    await shipped_order(t, x945("9301", ["LX*1", "W12*CP*12*12*0*EA**VN*CF0092"]))
    assert (await t.worker.run_once()).failed == 1
    del t.shop.fail["create_fulfillment"]
    return (await t.q("SELECT id FROM sales_shipments"))[0]["id"]


@pytest.mark.parametrize(("status", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_retry_and_a_write_in_each_state(p5, status, minutes, expected):  # noqa: F811
    sid = await failed_shipment(p5)
    for kind in (FULFILL, CLOSE):
        id = await put_row(p5, kind, {"shipment_id": sid}, ("sales_shipments", sid), status, minutes)
        e = await says(retry(p5, sid))
        assert said(e) == expected, kind
        if expected and expected[1] == "unknown":
            assert way_out(e)
        if expected is None:
            [r] = [r for r in await rows(p5, FULFILL, CLOSE) if r["status"] == "PENDING"]
            await set_row(p5, r["id"], status="FAILED")
            async with p5.factory(SYSTEM_ACTOR) as u:
                (await u.session.get(Shipment, sid)).shopify_status = "Failed"
                await u.commit()
        await set_row(p5, id, status="DONE")


async def test_a_retry_stuck_by_a_crash_says_how_it_ends_then_goes(p5):  # noqa: F811
    sid = await failed_shipment(p5)
    assert await says(retry(p5, sid)) is None
    [r] = [r for r in await rows(p5, FULFILL) if r["status"] == "PENDING"]
    e = await says(retry(p5, sid))
    assert said(e) == ("send.queued", "pending")
    await crash(p5, r["id"])
    e = await says(retry(p5, sid))  # its shipment says Pending: that alone would not say what to do
    assert said(e) == ("send.outcome_unknown", "unknown") and way_out(e)
    assert (await p5.worker.run_once()).failed == 1
    assert await says(retry(p5, sid)) is None
    await p5.worker.run_once()
    assert writes(p5).count("create_fulfillment") == 2  # the failed one, and the retry's


async def test_two_retries_at_once_write_once(p5):  # noqa: F811
    sid = await failed_shipment(p5)
    e = await twice(p5, OP, lambda u: OrderService(u).retry_shopify(sid, p5.ships))
    assert said(e) == ("send.queued", "pending")
    await p5.worker.run_once()
    assert writes(p5).count("create_fulfillment") == 2


async def test_a_retry_s_refusals_stand(p5, monkeypatch):  # noqa: F811
    sid = await failed_shipment(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Shipment, sid)).shopify_status = "Done"
        await u.commit()
    assert (await says(retry(p5, sid))).code == "shipment.not_failed"
    async with p5.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Shipment, sid)).shopify_status = "Failed"
        (await u.session.get(Shipment, sid)).shadow = True
        await u.commit()
    assert (await says(retry(p5, sid))).code == "shipment.shadow"

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(p5.factory, "permissions", reader)
    assert isinstance(await says(retry(p5, sid)), PermissionDenied)
    assert isinstance(await says(retry(p5, 99_999)), BusinessError)
