"""What came back of a line — returned on the order (set_returned) and recorded after shipment (FR-OB-19, both kinds, all records)
— is together at most what shipped (user 2026-11-24, 2026-11-25)."""
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
from app.schemas.orders import ReturnedIn, ReturnedLineIn
from app.services.order_views import OrderService
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


def refused(e, line, already, asked, on_order=0):
    assert (e.code, e.status_code, e.details) == ("return.more_than_shipped", 422, {"line_id": line, "field": "qty_ea"})
    assert e.message == (f"line 1: {on_order} EA returned on the order + {already} EA already recorded + {asked} EA asked is more than "
                         f"the {SHIPPED} EA shipped")


def edit(oid, *lines):
    async def go(u):
        await OrderService(u).set_returned(oid, ReturnedIn(reason="refused at the door", lines=[ReturnedLineIn(id=i, returned_ea=n) for i, n in lines]),
                                           ANY_VERSION)
    return go


async def set_to(t, oid, *lines):
    async def go():
        async with t.factory(OP) as u:
            await edit(oid, *lines)(u)
            await u.commit()
    return await says(go)


def edit_refused(e, line, new, recorded):
    assert (e.code, e.status_code, e.details) == ("return.more_than_shipped", 422, {"line_id": line, "field": "returned_ea"})
    assert e.message == f"line 1: {new} EA returned on the order + {recorded} EA already recorded is more than the {SHIPPED} EA shipped"


async def on_order(t, line):
    return (await t.q("SELECT returned_ea FROM sales_order_lines WHERE id = :i", i=line))[0]["returned_ea"]


async def put(t, line, returned):
    """As an old row stands, past the rule."""
    async with t.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(OrderLine, line)).returned_ea = returned
        await u.commit()


async def add_line(t, oid, line, no=99):
    async with t.factory(SYSTEM_ACTOR) as u:
        src = await u.session.get(OrderLine, line)
        more = OrderLine(order_id=oid, line_no=no, item_code=src.item_code, source={}, ordered_qty=src.ordered_qty, order_unit=src.order_unit,
                         factor_ea=src.factor_ea, qty_ea=src.qty_ea, shipped_ea=SHIPPED, line_status=src.line_status, removed=False)
        u.session.add(more)
        await u.session.flush()
        id = more.id
        await u.commit()
    return id


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
    assert "line 99: 0 EA returned on the order + 60 EA already recorded + 1 EA asked" in e.message


async def test_returned_on_the_order_first_then_a_record(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await set_to(p6, o.id, (line, 24)) is None
    before = await state(p6, o.id)
    refused(await ask(p6, o.id, (line, 37)), line, 0, 37, on_order=24)  # one over
    assert await state(p6, o.id) == before  # nothing written
    assert await ask(p6, o.id, (line, 12)) is None
    refused(await ask(p6, o.id, (line, 25)), line, 12, 25, on_order=24)  # the three add up
    assert await ask(p6, o.id, (line, 24)) is None  # exactly equal
    refused(await ask(p6, o.id, (line, 1)), line, 36, 1, on_order=24)


async def test_a_record_first_then_returned_on_the_order(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await ask(p6, o.id, (line, 24)) is None and await ask(p6, o.id, (line, 12), kind="Cancelled after shipment") is None
    before = await state(p6, o.id)
    edit_refused(await set_to(p6, o.id, (line, 25)), line, 25, 36)  # one over: both kinds, every record
    assert await state(p6, o.id) == before and await on_order(p6, line) == 0
    assert await set_to(p6, o.id, (line, 24)) is None  # exactly equal
    edit_refused(await set_to(p6, o.id, (line, 25)), line, 25, 36)
    assert await on_order(p6, line) == 24


async def test_an_earlier_figure_is_replaced_never_added(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await ask(p6, o.id, (line, 20)) is None
    assert await set_to(p6, o.id, (line, 30)) is None and await set_to(p6, o.id, (line, 40)) is None  # 40 + 20, not 30 + 40 + 20
    assert await on_order(p6, line) == 40


async def test_lowering_or_leaving_a_line_that_is_above_already_stays_possible(p6):  # noqa: F811
    o, line = await shipped(p6)
    other = await add_line(p6, o.id, line)
    assert await ask(p6, o.id, (line, 40)) is None
    await put(p6, line, 50)  # 50 + 40 of 60, from before the rule
    assert await set_to(p6, o.id, (line, 50), (other, 10)) is None  # left as it is, another line raised: not held against it
    assert (await on_order(p6, line), await on_order(p6, other)) == (50, 10)
    assert await set_to(p6, o.id, (line, 45), (other, 10)) is None  # lowered, still above
    assert await on_order(p6, line) == 45
    edit_refused(await set_to(p6, o.id, (line, 46)), line, 46, 40)  # but never up
    refused(await ask(p6, o.id, (line, 1)), line, 40, 1, on_order=45)
    assert await set_to(p6, o.id, (line, 0)) is None and await on_order(p6, line) == 0


async def test_another_line_s_figures_do_not_count_and_one_over_refuses_them_all(p6):  # noqa: F811
    o, line = await shipped(p6)
    other = await add_line(p6, o.id, line)
    assert await ask(p6, o.id, (other, 60)) is None
    assert await set_to(p6, o.id, (line, 60)) is None  # the other's record is not this line's
    assert await set_to(p6, o.id, (line, 0)) is None
    e = await set_to(p6, o.id, (line, 5), (other, 1))
    assert (e.code, e.details["line_id"]) == ("return.more_than_shipped", other) and "line 99: 1 EA returned on the order + 60 EA already recorded" in e.message
    assert await on_order(p6, line) == 0  # and the good line is not changed either


@pytest.mark.parametrize("first", ["the order", "the record"])
async def test_two_people_at_once_one_each_way_cannot_pass_it_together(p6, first):  # noqa: F811
    o, line = await shipped(p6)
    way = {"the order": edit(o.id, (line, 40)), "the record": record(p6, o.id, (line, 40))}
    other = next(k for k in way if k != first)
    e = await twice(p6, OP, way[first], way[other])
    if first == "the order":
        refused(e, line, 0, 40, on_order=40)
    else:
        edit_refused(e, line, 40, 40)
    assert (len(await kept(p6, o.id)), await on_order(p6, line)) == ((0, 40) if first == "the order" else (1, 0))


async def test_two_people_at_once_both_on_the_order_the_last_figure_stands(p6):  # noqa: F811
    o, line = await shipped(p6)
    assert await ask(p6, o.id, (line, 20)) is None
    edit_refused(await twice(p6, OP, edit(o.id, (line, 40)), edit(o.id, (line, 41))), line, 41, 20)
    assert await on_order(p6, line) == 40


async def test_what_the_unit_read_before_the_lock_is_read_again_under_it(p6):  # noqa: F811
    """A unit that had the line in memory (returned 0) when another person returned 40."""
    o, line = await shipped(p6)
    async with p6.factory(OP) as late:
        held = await late.session.get(OrderLine, line)  # kept: the unit has it in memory
        assert held.returned_ea == 0
        assert await set_to(p6, o.id, (line, 40)) is None
        refused(await says(lambda: record(p6, o.id, (line, 40))(late)), line, 0, 40, on_order=40)
        await late.rollback()


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
