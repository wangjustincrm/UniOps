"""Invoices and the version seen (stage 3 f): a confirmation from an old page never overwrites a returned quantity; a change
of lines moves the invoice whatever its total; bulk confirmation by order and version; and one order of locks (the order,
then its invoice) so no two of the operations wait for each other."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.models.invoices import Invoice
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import ANY_VERSION, Stale
from app.platform.errors import BusinessError, InvalidInput
from app.schemas.invoices import ConfirmIn, DeliveredLine, InvoiceEdit
from app.schemas.orders import ReturnedIn
from app.services.invoice_views import InvoiceService
from app.services.order_actions import ORDER_ACTIONS
from app.services.order_views import OrderService
from tests.test_invoicing import OP, confirm, inv_status, order_version, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


async def line_of(t, oid):
    return (await t.q("SELECT id, shipped_ea, returned_ea FROM sales_order_lines WHERE order_id = :o", o=oid))[0]


async def returned(t, svc, oid, line, ea, version=ANY_VERSION):
    return await svc.set_returned(oid, ReturnedIn(reason="refused", lines=[{"id": line, "returned_ea": ea}]), version)


async def waiting(t, po):
    o = await shipped_850(t, po, qty=10)
    await t.inv.advance()
    return o, await line_of(t, o.id)


async def test_a_confirmation_from_an_old_page_never_overwrites_a_returned_quantity(p6):  # noqa: F811
    o, ln = await waiting(p6, "PO-IV1")
    async with p6.factory(OP) as u:
        seen = await order_version(u, o.id)
        await returned(p6, OrderService(u), o.id, ln["id"], 12)
        await u.commit()
    body = dict(order_id=o.id, confirmed_date=date(2026, 11, 5), lines=[DeliveredLine(order_line_id=ln["id"], delivered_qty=ln["shipped_ea"])])
    for version, error in ((seen, Stale), (None, InvalidInput)):
        async with p6.factory(OP) as u:
            with pytest.raises(error):
                await InvoiceService(u).confirm(p6.inv, ConfirmIn(**body, version=version))
    assert (await line_of(p6, o.id))["returned_ea"] == 12 and await inv_status(p6, o.id) == "Waiting for Delivery Confirmation"
    async with p6.factory(OP) as u:  # who looked again is let through
        await InvoiceService(u).confirm(p6.inv, ConfirmIn(order_id=o.id, confirmed_date=date(2026, 11, 5), version=await order_version(u, o.id)))
        await u.commit()
    assert (await line_of(p6, o.id))["returned_ea"] == 12
    async with p6.factory(OP) as u:  # confirmed already: said as that, not as stale
        with pytest.raises(BusinessError) as e:
            await InvoiceService(u).confirm(p6.inv, ConfirmIn(**body, version=seen))
    assert e.value.code == "delivery.not_waiting"


async def test_a_change_of_lines_moves_the_invoice_whatever_its_total(p6):  # noqa: F811
    o, ln = await waiting(p6, "PO-IV2")
    inv = await confirm(p6, o.id)
    [il] = await p6.q("SELECT id, qty, unit_price FROM sales_invoice_lines WHERE invoice_id = :i", i=inv.id)
    body = InvoiceEdit(reason="same total", lines=[{"id": il["id"], "qty": il["qty"] * 2, "unit_price": str(il["unit_price"] / 2)}])
    async with p6.factory(OP) as a, p6.factory(OP) as b:
        await InvoiceService(a).edit(p6.inv, inv.id, body, inv.version)
        second = asyncio.create_task(InvoiceService(b).edit(p6.inv, inv.id, body, inv.version))
        await asyncio.sleep(0.3)
        assert not second.done()
        await a.commit()
        with pytest.raises(Stale):
            await second
        await b.rollback()
    v = (await p6.q("SELECT version FROM sales_invoices WHERE id = :i", i=inv.id))[0]["version"]
    assert v == inv.version + 1
    async with p6.factory(OP) as u:  # nothing changed: nothing moves
        await InvoiceService(u).edit(p6.inv, inv.id, InvoiceEdit(reason="none", lines=[{"id": il["id"]}]), v)
        await u.commit()
    assert (await p6.q("SELECT version FROM sales_invoices WHERE id = :i", i=inv.id))[0]["version"] == v
    async with p6.factory(SYSTEM_ACTOR) as u:  # a free line: what is returned changes no total
        await u.session.execute(sa.text("UPDATE sales_invoice_lines SET unit_price = 0, amount = 0 WHERE invoice_id = :i"), {"i": inv.id})
        await u.session.execute(sa.text("UPDATE sales_invoices SET subtotal = 0, total = 0 WHERE id = :i"), {"i": inv.id})
        await u.commit()
    async with p6.factory(OP) as u:  # a returned quantity changes the draft's lines: the invoice moves too
        await returned(p6, OrderService(u), o.id, ln["id"], 6)
        await u.commit()
    assert (await p6.q("SELECT version FROM sales_invoices WHERE id = :i", i=inv.id))[0]["version"] == v + 1


async def act(t, ids, rt=None, permissions=None, **kw):
    async with t.factory(OP) as u:
        out = await run_action(u, ORDER_ACTIONS, "confirm_delivery", ActionIn(ids=ids, **kw), rt or SimpleNamespace(invoicing=t.inv))
        await u.commit()
    return {r.id: r for r in out.results}


async def test_in_bulk_each_by_its_version_and_one_that_moved_fails_alone(p6):  # noqa: F811
    a, _ = await waiting(p6, "PO-IV3")
    b, lb = await waiting(p6, "PO-IV4")
    c, _ = await waiting(p6, "PO-IV5")
    async with p6.factory(OP) as u:
        seen = {x.id: await order_version(u, x.id) for x in (a, b, c)}
        await returned(p6, OrderService(u), b.id, lb["id"], 6)
        await u.commit()
    await confirm(p6, c.id)
    r = await act(p6, seen, input={"confirmed_date": "2026-11-05"})
    assert [r[x.id].status for x in (a, b, c)] == ["done", "failed", "skipped"]
    assert r[b.id].code == "entity.stale" and "Ready" in r[c.id].reason
    assert await inv_status(p6, a.id) == "Ready" and await inv_status(p6, b.id) == "Waiting for Delivery Confirmation"
    assert (await line_of(p6, b.id))["returned_ea"] == 6 and await p6.q("SELECT 1 FROM sales_outbox WHERE kind = 'send_invoice'") == []


async def test_in_bulk_sending_too_needs_invoice_manage_and_sends_each_once(p6, monkeypatch):  # noqa: F811
    a, _ = await waiting(p6, "PO-IV6")
    async with p6.factory(OP) as u:
        ids = {a.id: await order_version(u, a.id)}

    def only(*keys):
        async def check(session, actor, key):  # noqa: ARG001
            return key in keys
        return check
    was = p6.factory.permissions
    monkeypatch.setattr(p6.factory, "permissions", only("sales.read", "sales.order.operate"))
    with pytest.raises(PermissionDenied):
        await act(p6, ids, input={"confirmed_date": "2026-11-05", "send": True})
    assert await inv_status(p6, a.id) == "Waiting for Delivery Confirmation"  # refused before anything
    monkeypatch.setattr(p6.factory, "permissions", was)
    r = await act(p6, ids, input={"confirmed_date": "2026-11-05", "send": True})
    assert (r[a.id].status, r[a.id].note) == ("done", "sent")
    r = await act(p6, ids, input={"confirmed_date": "2026-11-05", "send": True})
    assert r[a.id].status == "skipped"
    assert len(await p6.q("SELECT 1 FROM sales_outbox WHERE kind = 'send_invoice'")) == 1


# ── one order of locks ───────────────────────────────────────────────────────

async def _edit(t, u, inv, ids):  # noqa: ARG001
    await InvoiceService(u).edit(t.inv, inv.id, InvoiceEdit(reason="x y z", delivery_number="POD"), ANY_VERSION)


async def _send(t, u, inv, ids):  # noqa: ARG001
    await InvoiceService(u).send(t.inv, inv.id)


async def _pre_check(t, u, inv, ids):  # noqa: ARG001
    await t.inv._pre_check({"invoice_id": inv.id}, u)


async def _pair(t, u, inv, ids):  # noqa: ARG001
    await t.inv._pair(SimpleNamespace(payload={"invoice_id": inv.id}), u)


async def _returned(t, u, inv, ids):
    await returned(t, OrderService(u), ids.order, ids.line, 6)


async def _confirm(t, u, inv, ids):  # noqa: ARG001
    with pytest.raises(BusinessError):  # confirmed already: it is refused holding the order, as it would go on
        await t.inv.confirm(u, ids.order, version=ANY_VERSION, confirmed_date=date(2026, 11, 5))


@pytest.mark.parametrize("second", [_returned, _confirm], ids=["returned", "confirm"])
@pytest.mark.parametrize("first", [_edit, _send, _pre_check, _pair], ids=["edit", "send", "pre_check", "done"])
async def test_the_order_is_locked_before_its_invoice_so_neither_waits_for_the_other(p6, first, second):  # noqa: F811
    """The other holds the order (as returned and confirm do first) when `first` comes: it must wait there without holding
    the invoice, or the other, going on to the invoice, would wait for it — a deadlock."""
    o, ln = await waiting(p6, "PO-IV7")
    inv = await confirm(p6, o.id)
    ids = SimpleNamespace(order=o.id, line=ln["id"])
    async with p6.factory(OP) as other, p6.factory(OP) as u:
        await other.session.get(Order, o.id, with_for_update=True)
        task = asyncio.create_task(first(p6, u, inv, ids))
        await asyncio.sleep(0.4)
        assert not task.done()  # waits for the order
        got = (await other.session.execute(sa.select(Invoice.id).where(Invoice.id == inv.id).with_for_update(skip_locked=True))).scalar()
        assert got == inv.id    # … and holds no invoice meanwhile
        await second(p6, other, inv, ids)
        await other.commit()
        await asyncio.wait_for(task, 5)
        await u.commit()


async def test_an_invoice_that_is_gone_is_said_as_before(p6):  # noqa: F811
    async with p6.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await InvoiceService(u).edit(p6.inv, 99_999, InvoiceEdit(reason="x y z"), 1)
        assert (e.value.code, e.value.status_code) == ("entity.not_found", 404)
        with pytest.raises(BusinessError) as e:
            await InvoiceService(u).send(p6.inv, 99_999)
        assert e.value.code == "invoice.not_found"
    async with p6.factory(SYSTEM_ACTOR) as u:
        assert await p6.inv._pre_check({"invoice_id": 99_999}, u) == "the invoice or its order is gone"
