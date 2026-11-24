"""Sending an invoice, and sending it again (stage 3 j): once, by the version seen, a repeat only with why, never past one
still under way. The fake SFTP behind the real guard."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.domain.status import Fulfillment as F
from app.models.config import Connection
from app.models.invoices import Invoice
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import ANY_VERSION, Stale
from app.platform.errors import BusinessError, InvalidInput
from app.platform.outbox import AcceptedThenFailed
from app.schemas.invoices import ConfirmIn, InvoiceEdit
from app.schemas.orders import ReturnedIn
from app.services.invoice_actions import INVOICE_ACTIONS
from app.services.invoice_views import InvoiceService
from app.services.invoicing import INTERNAL_COPY, SEND_INVOICE
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from app.services.settings import INVOICE_INTERNAL_EMAILS, SETTINGS, SHADOW_MODE
from tests.sending import STATE_IDS, STATES, crash, put_row, rows, said, says, twice, way_out
from tests.test_invoicing import OP, confirm, inv_status, order_version, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

WHY = "lost in the mail"


def puts(t):
    return [c for c, a in t.store.calls if c == "put"]


async def ready(t, po="PO-S1"):
    o = await shipped_850(t, po)
    await t.inv.advance()
    return o, await confirm(t, o.id, when=date(2026, 11, 5))


async def sent(t, po="PO-S1"):
    o, inv = await ready(t, po)
    assert await says(press(t, inv.id, await version(t, inv.id))) is None
    assert (await t.worker.run_once()).done == 1 and await inv_status(t, o.id) == "Sent"
    return o, inv


async def version(t, id):
    return (await t.q("SELECT version FROM sales_invoices WHERE id = :i", i=id))[0]["version"]


def press(t, id, v, again=False, reason=WHY):
    async def go():
        async with t.factory(OP) as u:
            await InvoiceService(u).send(t.inv, id, v, again=again, reason=reason if again else None)
            await u.commit()
    return go


async def act(t, action, id, v, **kw):
    async with t.factory(OP) as u:
        out = await run_action(u, INVOICE_ACTIONS, action, ActionIn(ids={id: v}, **kw), SimpleNamespace(invoicing=t.inv))
        await u.commit()
    return out.results[0]


async def queued(t):
    return [r for r in await rows(t, SEND_INVOICE) if r["status"] == "PENDING"]


@pytest.mark.parametrize("again", [False, True], ids=["first", "again"])
@pytest.mark.parametrize(("state", "minutes", "expected"), STATES, ids=STATE_IDS)
async def test_a_press_and_a_send_in_each_state(p6, state, minutes, expected, again):  # noqa: F811
    o, inv = await (sent if again else ready)(p6)
    n = len(await rows(p6, SEND_INVOICE))
    await put_row(p6, SEND_INVOICE, {"invoice_id": inv.id}, ("sales_invoices", inv.id), state, minutes)
    e = await says(press(p6, inv.id, await version(p6, inv.id), again))
    assert said(e) == expected
    if expected and expected[1] == "unknown":
        assert way_out(e)
    assert len(await rows(p6, SEND_INVOICE)) == n + 1 + (0 if expected else 1)
    assert await inv_status(p6, o.id) == ("Sent" if again else "Ready" if expected else "Queued")


async def test_the_company_s_copy_and_another_invoice_s_row_do_not_block(p6):  # noqa: F811
    _, inv = await sent(p6)
    for state, minutes in (("PENDING", -1), ("IN_FLIGHT", 10), ("IN_FLIGHT", -10)):
        await put_row(p6, INTERNAL_COPY, {"invoice_id": inv.id}, ("sales_invoices", inv.id), state, minutes)
    await put_row(p6, SEND_INVOICE, {"invoice_id": inv.id + 1}, ("sales_invoices", inv.id + 1), "PENDING")
    assert await says(press(p6, inv.id, await version(p6, inv.id), True)) is None


async def test_a_send_lost_in_a_crash_says_how_it_ends_then_can_be_sent(p6):  # noqa: F811
    o, inv = await ready(p6)
    assert await says(press(p6, inv.id, await version(p6, inv.id))) is None
    [r] = await queued(p6)
    await crash(p6, r["id"])
    e = await says(press(p6, inv.id, await version(p6, inv.id)))  # the invoice says Queued: that alone would not say what to do
    assert said(e) == ("send.outcome_unknown", "unknown") and way_out(e)
    assert (await p6.worker.run_once()).failed == 1 and puts(p6) == []  # closed, not run again
    assert await inv_status(p6, o.id) == "Failed"
    assert await says(press(p6, inv.id, await version(p6, inv.id))) is None
    assert (await p6.worker.run_once()).done == 1 and len(puts(p6)) == 1


@pytest.mark.parametrize("again", [False, True], ids=["first", "again"])
async def test_two_presses_at_once_send_once(p6, again):  # noqa: F811
    _, inv = await (sent if again else ready)(p6)
    v, before = await version(p6, inv.id), len(puts(p6))
    e = await twice(p6, OP, lambda u: InvoiceService(u).send(p6.inv, inv.id, v, again=again, reason=WHY))
    assert said(e) == ("send.queued", "pending") and len(await queued(p6)) == 1
    await p6.worker.run_once()
    assert len(puts(p6)) == before + 1


async def test_a_sent_invoice_is_sent_again_only_on_purpose_with_why_once(p6):  # noqa: F811
    _, inv = await sent(p6)
    v = await version(p6, inv.id)
    e = await says(press(p6, inv.id, v))  # a page that does not know it was sent
    assert (e.code, e.details) == ("invoice.already_sent", {"needs": "again"})
    for reason in (None, "", " ok "):
        e = await says(press(p6, inv.id, v, True, reason))
        assert (e.status_code, e.details) == (422, {"field": "reason"})
    with pytest.raises(BusinessError) as x:
        await act(p6, "send_again", inv.id, v)
    assert (x.value.status_code, x.value.details) == (422, {"field": "reason"})
    assert await queued(p6) == []
    assert await says(press(p6, inv.id, v, True)) is None
    await p6.worker.run_once()
    assert isinstance(await says(press(p6, inv.id, v, True)), Stale)  # sent since: the second person looks again
    assert len(puts(p6)) == 2 and (await p6.q("SELECT send_count FROM sales_invoices WHERE id = :i", i=inv.id))[0]["send_count"] == 2


async def test_why_is_kept_in_the_invoice_s_activity_with_who_asked(p6):  # noqa: F811
    _, inv = await sent(p6)
    assert await says(press(p6, inv.id, await version(p6, inv.id), True, f"  {WHY} ")) is None
    async with p6.factory(OP) as u:
        timeline = (await InvoiceService(u).detail(inv.id)).timeline
    asked = [e for e in timeline if e.action == "send_again"]
    assert [(e.user_name, e.reason, e.before, e.after) for e in asked] == [(OP.name, f"invoice sent again: {WHY}", None, None)]


async def test_the_press_alone_moves_the_invoice(p6):  # noqa: F811
    _, inv = await sent(p6)
    v = await version(p6, inv.id)
    assert await says(press(p6, inv.id, v, True)) is None
    assert await version(p6, inv.id) == v + 1


async def test_a_first_send_asks_for_nothing_but_the_version(p6):  # noqa: F811
    _, inv = await ready(p6)
    v = await version(p6, inv.id)
    assert isinstance(await says(press(p6, inv.id, v + 1)), Stale) and isinstance(await says(press(p6, inv.id, None)), InvalidInput)
    assert (await says(press(p6, inv.id, v, True))).code == "invoice.not_sent_yet"
    assert await queued(p6) == []
    assert await says(press(p6, inv.id, v)) is None and len(await queued(p6)) == 1


async def test_an_invoice_edited_since_is_looked_at_again_before_it_goes(p6):  # noqa: F811
    _, inv = await ready(p6)
    v = await version(p6, inv.id)
    async with p6.factory(OP) as u:
        d = await InvoiceService(u).detail(inv.id)
        await InvoiceService(u).edit(p6.inv, inv.id, InvoiceEdit(lines=[{"id": d.lines[0].id, "qty": 9}]), v)
        await u.commit()
    assert isinstance(await says(press(p6, inv.id, v)), Stale) and await queued(p6) == []


async def test_a_failed_and_a_refused_one_can_be_sent_again_a_delivered_one_not_by_itself(p6):  # noqa: F811
    o, inv = await ready(p6)

    async def go(again=False):
        assert await says(press(p6, inv.id, await version(p6, inv.id), again)) is None
        return await p6.worker.run_once()

    p6.store.fail["put"] = RuntimeError("connection reset")
    assert (await go()).failed == 1 and await inv_status(p6, o.id) == "Failed"
    del p6.store.fail["put"]
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, p6.edi.conn_id)).can_write = False
        await u.commit()
    assert (await go()).suppressed == 1 and await inv_status(p6, o.id) == "Failed"  # the write gate
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, p6.edi.conn_id)).can_write = True
        await u.commit()
    p6.store.fail["put"] = AcceptedThenFailed("delivered, then the line dropped")
    assert (await go()).done == 1 and await inv_status(p6, o.id) == "Sent"
    n = len(puts(p6))
    assert (await p6.worker.run_once()).processed == 0 and len(puts(p6)) == n  # never again by itself
    p6.store.fail["put"] = RuntimeError("connection reset")
    assert (await go(again=True)).failed == 1 and await inv_status(p6, o.id) == "Sent"  # the repeat failed; it was sent before
    del p6.store.fail["put"]
    assert (await says(press(p6, inv.id, await version(p6, inv.id)))).code == "invoice.already_sent"  # still a repeat: why again
    assert (await go(again=True)).done == 1


async def test_each_refusal_stands_and_queues_nothing(p6, monkeypatch):  # noqa: F811
    o, inv = await ready(p6)

    async def set_(order=None, invoice=None, partner=None, shadow=None):
        async with p6.factory(SYSTEM_ACTOR) as u:
            s = u.session
            for row, values in ((await s.get(Order, o.id), order), (await s.get(Invoice, inv.id), invoice)):
                for k, v in (values or {}).items():
                    setattr(row, k, v)
            if shadow is not None:
                await SETTINGS.set(s, SHADOW_MODE, shadow)
            await u.commit()

    async def refused(code):
        for again, action in ((False, "send"), (True, "send_again")):
            v = await version(p6, inv.id)
            e = await says(press(p6, inv.id, v, again))
            assert e.code == code
            r = await act(p6, action, inv.id, v, reason=WHY)
            assert r.status == "skipped" and r.reason == e.message

    for change, code, back in (
            (dict(order={"shadow": True}), "invoice.shadow_order", dict(order={"shadow": False})),
            (dict(shadow=True), "invoice.shadow", dict(shadow=False)),
            (dict(order={"fulfillment_status": F.CANCELLED.value}), "invoice.order_cancelled", dict(order={"fulfillment_status": F.SHIPPED.value})),
            (dict(order={"invoice_status": "Blocked", "invoice_status_before_hold": "Ready"}), "invoice.not_sendable", dict(order={"invoice_status": "Ready"})),
            (dict(order={"invoice_status": "Queued"}), "invoice.not_sendable", dict(order={"invoice_status": "Ready"})),
            (dict(invoice={"method": "NONE"}), "invoice.method_none", dict(invoice={"method": "810_JSON"}))):
        await set_(**change)
        await refused(code)
        await set_(**back)
    was, v = p6.factory.permissions, await version(p6, inv.id)

    async def operator(session, actor, key):  # noqa: ARG001
        return key in ("sales.read", "sales.order.operate")
    monkeypatch.setattr(p6.factory, "permissions", operator)
    assert isinstance(await says(press(p6, inv.id, v)), PermissionDenied)
    for action in ("send", "send_again"):
        with pytest.raises(PermissionDenied):
            await act(p6, action, inv.id, v, reason=WHY)
    monkeypatch.setattr(p6.factory, "permissions", was)
    assert await rows(p6, SEND_INVOICE) == [] and puts(p6) == []
    r = await act(p6, "send", inv.id, v - 1)
    assert (r.status, r.code) == ("failed", "entity.stale")
    assert (await act(p6, "send", inv.id, v)).status == "done"  # and it is admitted
    r = await act(p6, "send", inv.id, v)
    assert r.status == "skipped" and "already queued" in r.reason
    await p6.worker.run_once()
    r = await act(p6, "send", inv.id, v)
    assert r.status == "skipped" and "sent already" in r.reason  # what happened, whatever the version
    assert (await act(p6, "send_again", inv.id, await version(p6, inv.id), reason=WHY)).status == "done"


async def test_one_at_a_time(p6):  # noqa: F811
    for action in INVOICE_ACTIONS.actions:
        async with p6.factory(OP) as u:
            with pytest.raises(InvalidInput) as e:
                await run_action(u, INVOICE_ACTIONS, action, ActionIn(ids={1: 1, 2: 1}, reason=WHY))
            assert e.value.code == "action.single"


# ── the order, then its invoice: as a record action too ──────────────────────

@pytest.mark.parametrize("action", ["send", "send_again"])
async def test_an_invoice_action_locks_the_order_before_its_invoice(p6, action):  # noqa: F811
    """Another holds the order (a returned quantity): the action waits there holding no invoice — neither waits for the other."""
    o, inv = await (ready if action == "send" else sent)(p6)
    line = (await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :o", o=o.id))[0]["id"]
    v = await version(p6, inv.id)
    async with p6.factory(OP) as other:
        await other.session.get(Order, o.id, with_for_update=True)
        task = asyncio.create_task(act(p6, action, inv.id, v, reason=WHY))
        await asyncio.sleep(0.4)
        free = (await other.session.execute(sa.select(Invoice.id).where(Invoice.id == inv.id).with_for_update(skip_locked=True))).scalar()
        if task.done() or free != inv.id:
            await other.rollback()
            await asyncio.wait({task}, timeout=5)  # let it end before the test's teardown
            pytest.fail("the action did not wait for the order, or held the invoice meanwhile")
        if action == "send":
            await OrderService(other).set_returned(o.id, ReturnedIn(reason="refused", lines=[{"id": line, "returned_ea": 6}]), ANY_VERSION)
        await other.commit()
        r = await asyncio.wait_for(task, 5)
    assert (r.status, r.code) == (("failed", "entity.stale") if action == "send" else ("done", None))  # the return moved the invoice


async def test_an_invoice_that_is_gone_is_said(p6):  # noqa: F811
    r = await act(p6, "send", 99_999, 1)
    assert (r.status, r.code) == ("failed", "entity.not_found")


# ── confirm and send ─────────────────────────────────────────────────────────

async def test_confirm_and_send_one_by_two_people_sends_once(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-S7")
    await p6.inv.advance()
    async with p6.factory(OP) as u:
        body = ConfirmIn(order_id=o.id, version=await order_version(u, o.id), confirmed_date=date(2026, 11, 5), send=True)
    e = await twice(p6, OP, lambda u: InvoiceService(u).confirm(p6.inv, body))
    assert e.code == "delivery.not_waiting" and len(await queued(p6)) == 1
    await p6.worker.run_once()
    assert len(puts(p6)) == 1 and await inv_status(p6, o.id) == "Sent"


async def test_bulk_confirm_and_send_one_that_moved_stays_the_others_go_once(p6):  # noqa: F811
    orders = []
    for po in ("PO-S8", "PO-S9", "PO-S10"):
        orders.append(await shipped_850(p6, po))
    await p6.inv.advance()
    async with p6.factory(OP) as u:
        ids = {o.id: await order_version(u, o.id) for o in orders}
    ids[orders[1].id] -= 1  # seen before it moved
    body = ActionIn(ids=ids, input={"confirmed_date": "2026-11-05", "send": True})
    for expected in (["done", "failed", "done"], ["skipped", "failed", "skipped"]):
        async with p6.factory(OP) as u:
            out = await run_action(u, ORDER_ACTIONS, "confirm_delivery", body, SimpleNamespace(invoicing=p6.inv))
            await u.commit()
        assert [r.status for r in out.results] == expected and len(await queued(p6)) == 2
    assert (await p6.worker.run_once()).done == 2 and len(puts(p6)) == 2
    assert await inv_status(p6, orders[1].id) == "Waiting for Delivery Confirmation"


async def test_the_company_s_copy_goes_once_with_each_send(p6):  # noqa: F811
    async with p6.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, INVOICE_INTERNAL_EMAILS, ["finance@crm.example"])
        await u.commit()
    _, inv = await ready(p6)
    v = await version(p6, inv.id)
    await twice(p6, OP, lambda u: InvoiceService(u).send(p6.inv, inv.id, v))
    assert [r["kind"] for r in await rows(p6, SEND_INVOICE, INTERNAL_COPY)].count(INTERNAL_COPY) == 1
