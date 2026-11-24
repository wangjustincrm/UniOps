"""Recorded returns (FR-OB-19) of a line add up to at most what shipped (user 2026-11-24), both kinds, all records together."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.invoices import OrderReturn
from app.models.orders import Order, OrderLine
from app.platform.actors import SYSTEM_ACTOR
from app.platform.commands import ANY_VERSION, Stale
from app.platform.errors import BusinessError
from app.services.invoice_views import InvoiceService
from app.schemas.invoices import ReturnIn
from tests.sending import says, twice
from tests.test_invoicing import OP, inv_status, order_version, p6, shipped_850, tasks  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture

SHIPPED = 60


async def shipped(t, po="PO-RT1"):
    o = await shipped_850(t, po)
    line = (await t.q("SELECT id, line_no, shipped_ea FROM sales_order_lines WHERE order_id = :o", o=o.id))[0]
    assert line["shipped_ea"] == SHIPPED
    return o, line["id"]


def record(t, oid, *lines, kind="Returned"):
    """Straight into the operation, with no version: the rule itself is what answers."""
    async def go(u):
        await t.inv.record_return(u, oid, version=ANY_VERSION, kind=kind, reason="damaged in transit",
                                  lines=[{"order_line_id": i, "qty_ea": n} for i, n in lines])
    return go


async def ask(t, oid, *lines, kind="Returned"):
    async def go():
        async with t.factory(OP) as u:
            await record(t, oid, *lines, kind=kind)(u)
            await u.commit()
    return await says(go)


async def kept(t, oid):
    return [r["lines"] for r in await t.q("SELECT lines FROM sales_order_returns WHERE order_id = :o ORDER BY id", o=oid)]


async def state(t, oid):
    v = (await t.q("SELECT version FROM sales_orders WHERE id = :i", i=oid))[0]["version"]
    return SimpleNamespace(returns=await kept(t, oid), status=await inv_status(t, oid), version=v, tasks=len(await tasks(t, "order_return")))


def refused(e, line, already, asked):
    assert (e.code, e.status_code, e.details) == ("return.more_than_shipped", 422, {"line_id": line, "field": "qty_ea"})
    assert f"{already} EA already recorded + {asked} EA asked" in e.message and f"{SHIPPED} EA shipped" in e.message and "line 1:" in e.message


async def test_two_lines_of_one_request_add_up_and_a_refusal_changes_nothing(p6):  # noqa: F811
    o, line = await shipped(p6)
    before = await state(p6, o.id)
    refused(await ask(p6, o.id, (line, 40), (line, 21)), line, 0, 61)
    assert await state(p6, o.id) == before and before.returns == []  # no row, no hold, no task, no new version
    assert await ask(p6, o.id, (line, 40), (line, 20)) is None  # exactly what shipped


async def test_two_requests_add_up_to_exactly_what_shipped_and_no_more(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await ask(p6, o.id, (line, 24)) is None
    before = await state(p6, o.id)
    refused(await ask(p6, o.id, (line, 37)), line, 24, 37)
    assert await state(p6, o.id) == before
    assert await ask(p6, o.id, (line, 36)) is None  # exactly equal
    refused(await ask(p6, o.id, (line, 1)), line, 60, 1)
    assert len(await kept(p6, o.id)) == 2 and before.status == "Blocked"


async def test_both_kinds_count(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await ask(p6, o.id, (line, 50), kind="Cancelled after shipment") is None
    refused(await ask(p6, o.id, (line, 11)), line, 50, 11)
    assert await ask(p6, o.id, (line, 10)) is None
    refused(await ask(p6, o.id, (line, 1), kind="Cancelled after shipment"), line, 60, 1)


async def test_another_order_s_and_another_line_s_records_do_not_count(p6):  # noqa: F811
    o, line = await shipped(p6)
    other, _ = await shipped(p6, "PO-RT2")
    async with p6.factory(SYSTEM_ACTOR) as u:
        s = u.session
        src = await s.get(OrderLine, line)
        more = OrderLine(order_id=o.id, line_no=99, item_code=src.item_code, source={}, ordered_qty=src.ordered_qty, order_unit=src.order_unit,
                         factor_ea=src.factor_ea, qty_ea=src.qty_ea, shipped_ea=SHIPPED, line_status=src.line_status, removed=False)
        s.add(more)
        s.add(OrderReturn(order_id=other.id, kind="Returned", reason="another order", lines=[{"order_line_id": line, "qty_ea": 60}]))
        await s.flush()
        second = more.id
        await u.commit()
    assert await ask(p6, o.id, (second, 60)) is None
    assert await ask(p6, o.id, (line, 60)) is None
    e = await ask(p6, o.id, (second, 1), (line, 1))
    assert (e.code, e.details["line_id"]) == ("return.more_than_shipped", second)  # line 99 is over, its own figures
    assert "line 99: 60 EA already recorded + 1 EA asked" in e.message


async def test_what_the_customer_returned_before_the_invoice_is_another_figure(p6):  # noqa: F811
    o, line = await shipped(p6)
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(OrderLine, line)).returned_ea = 30
        await u.commit()
    assert await ask(p6, o.id, (line, 60)) is None  # not mixed with this rule


async def test_two_sessions_at_once_one_wins_one_row_one_hold_one_task(p6):  # noqa: F811
    o, line = await shipped(p6)
    e = await twice(p6, OP, record(p6, o.id, (line, 40)))
    refused(e, line, 40, 40)
    after = await state(p6, o.id)
    assert len(after.returns) == 1 and after.status == "Blocked" and after.tasks == 1


async def test_two_sessions_by_the_same_page_the_second_looks_again(p6):  # noqa: F811
    o, line = await shipped(p6)
    async with p6.factory(OP) as u:
        body = ReturnIn(order_id=o.id, version=await order_version(u, o.id), kind="Returned", reason="damaged", lines=[
            {"order_line_id": line, "qty_ea": 10}])
    e = await twice(p6, OP, lambda u: InvoiceService(u).record_return(p6.inv, body))
    assert isinstance(e, Stale) and len(await kept(p6, o.id)) == 1


async def test_the_earlier_refusals_stand(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert (await ask(p6, o.id, (line, 61))).code == "return.quantity"      # one line above what shipped, as before
    assert (await ask(p6, o.id, (line + 999, 1))).code == "return.quantity"
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Order, o.id)).fulfillment_status = "Pending"
        await u.commit()
    assert (await ask(p6, o.id, (line, 1))).code == "return.not_shipped"
    async with p6.factory(OP) as u:
        assert (await u.session.execute(select(func.count(OrderReturn.id)))).scalar_one() == 0
        with pytest.raises(BusinessError):
            await record(p6, 99_999, (line, 1))(u)
