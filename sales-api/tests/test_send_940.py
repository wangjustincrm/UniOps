"""Send 940 now and Replace 940 (stage 3 i): once, by the version seen, a replacement with why, never past a 940 still under
way. The fake SFTP behind the real guard."""
import asyncio
from types import SimpleNamespace

import pytest

from app.domain.status import Fulfillment as F
from app.models.config import Connection
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import Stale
from app.platform.errors import BusinessError, InvalidInput
from app.platform.outbox import AcceptedThenFailed
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from app.services.outbound import SEND_940
from app.services.settings import SETTINGS, SHADOW_MODE
from tests.sending import STATE_IDS, STATES, crash, put_row, rows, said, says, twice, way_out
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import a_paid_order, ack997, docs, edi, sent_order, status, store_997  # noqa: F401 — edi is a fixture

WHY = "the quantity was wrong"


@pytest.fixture
def t(edi):  # noqa: F811
    return SimpleNamespace(edi=edi, factory=edi.env.factory, q=edi.env.q, store=edi.store, worker=edi.worker)


def puts(t):
    return [c for c, _ in t.store.calls if c == "put"]


async def version(t, oid):
    return (await t.q("SELECT version FROM sales_orders WHERE id = :i", i=oid))[0]["version"]


def press(t, oid, v, replace=False, reason=WHY):
    async def go():
        async with t.factory(OP) as u:
            await OrderService(u).send_940_now(oid, t.edi.out, replace=replace, version=v, reason=reason if replace else None)
            await u.commit()
    return go


async def act(t, id, oid, v, **kw):
    async with t.factory(OP) as u:
        out = await run_action(u, ORDER_ACTIONS, id, ActionIn(ids={oid: v}, **kw), SimpleNamespace(outbound=t.edi.out))
        await u.commit()
    return out.results[0]


async def files(t):
    return [d["file_status"] for d in await docs(t.edi)]


async def set_order(t, oid, **values):
    async with t.factory(SYSTEM_ACTOR) as u:
        o = await u.session.get(Order, oid)
        for k, v in values.items():
            setattr(o, k, v)
        await u.commit()


@pytest.mark.parametrize("replace", [False, True], ids=["send", "replace"])
@pytest.mark.parametrize(("state", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_press_and_a_940_in_each_state(t, state, minutes, expected, replace):
    o = (await sent_order(t.edi))[0] if replace else await a_paid_order(t.edi)
    n = len(await docs(t.edi))
    await put_row(t, SEND_940, {"document_id": 1, "order_id": o.id}, ("sales_orders", o.id), state, minutes)
    e = await says(press(t, o.id, await version(t, o.id), replace))
    assert said(e) == expected
    if expected and expected[1] == "unknown":
        assert way_out(e)
    assert len(await docs(t.edi)) == n + (0 if expected else 1)  # refused: no file, no control number
    assert await status(t.edi, o.id) == ((F.SENT_940 if replace else F.PENDING) if expected else F.GENERATED_940)


async def test_another_order_s_and_another_kind_s_row_do_not_block(t):
    o = await a_paid_order(t.edi)
    await put_row(t, SEND_940, {}, ("sales_orders", o.id + 1), "PENDING")
    await put_row(t, "shopify_fulfill_997", {}, ("sales_orders", o.id), "PENDING")
    assert await says(press(t, o.id, await version(t, o.id))) is None


async def test_a_940_lost_in_a_crash_says_how_it_ends_then_can_be_sent(t):
    o = await a_paid_order(t.edi)
    assert await says(press(t, o.id, await version(t, o.id))) is None
    [r] = await rows(t, SEND_940)
    await crash(t, r["id"])
    e = await says(press(t, o.id, await version(t, o.id)))  # the order says 940 Generated: that alone would not say what to do
    assert said(e) == ("send.outcome_unknown", "unknown") and way_out(e)
    assert (await t.worker.run_once()).failed == 1 and puts(t) == []  # closed, not run again
    assert await status(t.edi, o.id) == F.FAILED_940
    assert await says(press(t, o.id, await version(t, o.id))) is None
    assert (await t.worker.run_once()).done == 1 and len(puts(t)) == 1


@pytest.mark.parametrize("replace", [False, True], ids=["send", "replace"])
async def test_two_presses_at_once_make_one_940(t, replace):
    o = (await sent_order(t.edi))[0] if replace else await a_paid_order(t.edi)
    v = await version(t, o.id)
    before = len(puts(t))
    e = await twice(t, OP, lambda u: OrderService(u).send_940_now(o.id, t.edi.out, replace=replace, version=v, reason=WHY))
    assert said(e) == ("send.queued", "pending")
    ctls = [d["st02"] for d in await docs(t.edi)]
    assert len(ctls) == len(set(ctls)) == (2 if replace else 1)
    await t.worker.run_once()
    assert len(puts(t)) == before + 1


async def test_a_second_person_does_not_replace_the_replacement(t):
    o, _ = await sent_order(t.edi)
    seen = await version(t, o.id)
    assert await says(press(t, o.id, seen, True)) is None
    await t.worker.run_once()  # sent: nothing is under way any more, the new one is live
    assert isinstance(await says(press(t, o.id, seen, True)), Stale)
    assert await files(t) == ["Replaced", "Sent"]
    assert isinstance(await says(press(t, o.id, None, True)), InvalidInput)
    assert await says(press(t, o.id, await version(t, o.id), True)) is None  # who looked again may


async def test_a_replacement_says_why_on_the_server_and_it_is_kept(t):
    o, _ = await sent_order(t.edi)
    v = await version(t, o.id)
    for reason in (None, "", "  ok "):
        e = await says(press(t, o.id, v, True, reason))
        assert (e.status_code, e.details) == (422, {"field": "reason"})
    with pytest.raises(BusinessError) as x:
        await act(t, "replace_940", o.id, v)
    assert (x.value.status_code, x.value.details) == (422, {"field": "reason"})
    assert await files(t) == ["Sent"]
    assert await says(press(t, o.id, v, True, f"  {WHY} ")) is None
    async with t.factory(OP) as u:
        timeline = (await OrderService(u).detail(o.id)).timeline
    assert next(e for e in timeline if e.entity == "sales_orders" and (e.after or {}).get("fulfillment_status") == F.GENERATED_940.value
                ).reason == f"940 replaced: {WHY}"
    [d] = await t.q("SELECT body_json->>'reason' r FROM sales_documents WHERE kind = '940_EDI' AND file_status = 'Ready To Send'")
    assert WHY in d["r"]


async def test_a_first_send_asks_for_nothing_but_the_version(t):
    o = await a_paid_order(t.edi)
    v = await version(t, o.id)
    assert isinstance(await says(press(t, o.id, v + 1)), Stale) and isinstance(await says(press(t, o.id, None)), InvalidInput)
    assert await files(t) == []
    assert await says(press(t, o.id, v)) is None and await files(t) == ["Ready To Send"]


async def test_a_failed_a_refused_and_a_rejected_one_can_be_sent_again_a_delivered_one_not_by_itself(t):
    o = await a_paid_order(t.edi)

    async def go():
        assert await says(press(t, o.id, await version(t, o.id))) is None
        return await t.worker.run_once()

    t.store.fail["put"] = RuntimeError("connection reset")
    assert (await go()).failed == 1 and await status(t.edi, o.id) == F.FAILED_940
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, t.edi.conn_id)).can_write = False
        await u.commit()
    del t.store.fail["put"]
    assert (await go()).suppressed == 1 and await status(t.edi, o.id) == F.FAILED_940  # the write gate
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, t.edi.conn_id)).can_write = True
        await u.commit()
    t.store.fail["put"] = AcceptedThenFailed("delivered, then the line dropped")
    assert (await go()).done == 1 and await status(t.edi, o.id) == F.SENT_940
    del t.store.fail["put"]
    n = len(puts(t))
    assert (await t.worker.run_once()).processed == 0 and len(puts(t)) == n  # never again by itself
    assert (await says(press(t, o.id, await version(t, o.id)))).code == "send940.live"
    last = (await docs(t.edi))[-1]
    await store_997(t.edi, ack997(last["st02"], "R", "R", "4"))
    await t.edi.out.process_997s()
    assert (await go()).done == 1  # rejected: a new one, with a new number


async def test_each_refusal_stands_and_makes_nothing(t, monkeypatch):
    o = await a_paid_order(t.edi)

    async def refused(code, replace=False):
        v = await version(t, o.id)
        assert (await says(press(t, o.id, v, replace))).code == code
        r = await act(t, "replace_940" if replace else "send_940", o.id, v, **({"reason": WHY} if replace else {}))
        assert r.status == "skipped" and r.reason, code

    await refused("send940.nothing_to_replace", True)
    for values, code, back in (({"historical": True}, "send940.historical", {"historical": False}),
                               ({"shadow": True}, "send940.shadow_order", {"shadow": False}),
                               ({"fulfillment_status": F.CANCELLED.value}, "send940.not_allowed", {"fulfillment_status": F.PENDING.value}),
                               ({"fulfillment_status": F.ON_HOLD.value}, "send940.not_allowed", {"fulfillment_status": F.PENDING.value}),
                               ({"fulfillment_status": F.GENERATED_940.value}, "send940.in_progress", {"fulfillment_status": F.PENDING.value}),
                               ({"shopify_financial_status": "pending"}, "send940.not_eligible", {"shopify_financial_status": "paid"})):
        await set_order(t, o.id, **values)
        await refused(code)
        await set_order(t, o.id, **back)
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    await refused("send940.shadow")
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, False)
        (await u.session.get(Connection, t.edi.conn_id)).active = False
        await u.commit()
    v = await version(t, o.id)
    assert (await says(press(t, o.id, v))).code == "edi.no_connection"
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, t.edi.conn_id)).active = True
        await u.commit()
    was = t.factory.permissions

    async def reader(session, actor, key):  # noqa: ARG001
        return key == "sales.read"
    monkeypatch.setattr(t.factory, "permissions", reader)
    assert isinstance(await says(press(t, o.id, v)), PermissionDenied)
    for id in ("send_940", "replace_940"):
        with pytest.raises(PermissionDenied):
            await act(t, id, o.id, v, reason=WHY)
    monkeypatch.setattr(t.factory, "permissions", was)
    assert await files(t) == [] and await rows(t, SEND_940) == [] and puts(t) == []
    r = await act(t, "send_940", o.id, v - 1)
    assert (r.status, r.code) == ("failed", "entity.stale")
    r = await act(t, "send_940", o.id, v)  # and it is admitted
    assert r.status == "done" and "queued" in r.note
    r = await act(t, "send_940", o.id, v)
    assert r.status == "skipped" and "already queued" in r.reason


async def test_one_at_a_time_and_no_940_in_bulk(t):
    for id in ("send_940", "replace_940", "cancel", "fulfil_in_shopify"):
        async with t.factory(OP) as u:
            with pytest.raises(InvalidInput) as e:
                await run_action(u, ORDER_ACTIONS, id, ActionIn(ids={1: 1, 2: 1}, reason=WHY, input={"to": "Cancelled"}))
            assert e.value.code == "action.single"


async def test_the_worker_s_claim_and_a_press_do_not_wait_for_each_other(t):
    """The outbox locks the document, then the order; a press holds the order: it must refuse before it wants the document."""
    o = await a_paid_order(t.edi)
    assert await says(press(t, o.id, await version(t, o.id))) is None
    [d] = await docs(t.edi)
    from app.models.documents import Document
    async with t.factory(SYSTEM_ACTOR) as worker, t.factory(OP) as u:
        await worker.session.get(Document, d["id"], with_for_update=True)
        await u.session.get(Order, o.id, with_for_update=True)
        with pytest.raises(BusinessError) as e:
            await asyncio.wait_for(OrderService(u).send_940_now(o.id, t.edi.out, version=await version(t, o.id)), 3)
        assert said(e.value) == ("send.queued", "pending")
