"""What the customer returned (or refused) of what shipped (user 2026-11-12): entered per line
with a reason, before the invoice goes. Delivered = shipped − returned; the invoice bills the
delivered — at confirmation, and on a draft that already exists. Once sent it is locked."""
import pytest

from app.platform.errors import BusinessError
from app.schemas.orders import ReturnedIn, ReturnedLineIn
from app.services.order_views import OrderService
from tests.test_invoicing import OP, confirm, p6, send, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


async def set_returned(t, oid, line_id, ea, reason="customer refused 2 cases"):
    async with t.factory(OP) as u:
        out = await OrderService(u).set_returned(oid, ReturnedIn(reason=reason, lines=[ReturnedLineIn(id=line_id, returned_ea=ea)]))
        await u.commit()
        return out


async def test_returned_before_confirmation_makes_the_invoice_bill_the_rest(p6):  # noqa: F811
    o = await shipped_850(p6, qty=10)  # 10 CA = 60 EA shipped
    d = await set_returned(p6, o.id, await _line(p6, o.id), 12)
    assert (d.lines[0].shipped_ea, d.lines[0].returned_ea, d.returned_ea) == (60, 12, 12)
    await p6.inv.advance()
    inv = await confirm(p6, o.id)
    assert [(ln.qty, str(ln.amount)) for ln in inv.lines] == [(8, "400.00")]  # 48 EA = 8 cases
    assert (await _detail(p6, o.id)).lines[0].delivered_qty == 48


async def test_a_return_after_the_draft_follows_on_it_until_it_is_sent(p6):  # noqa: F811
    o = await shipped_850(p6, number="PO-2", qty=10)
    await p6.inv.advance()
    inv = await confirm(p6, o.id)
    assert inv.lines[0].qty == 10
    lid = await _line(p6, o.id)
    await set_returned(p6, o.id, lid, 6)
    d = await _detail(p6, o.id)
    assert (d.lines[0].delivered_qty, d.lines[0].invoiced_qty, str(d.lines[0].invoice_amount)) == (54, 9, "450.00")
    with pytest.raises(BusinessError) as e:  # a part case cannot be invoiced
        await set_returned(p6, o.id, lid, 7)
    assert e.value.code == "returned.part_case"
    await send(p6, inv.id)
    with pytest.raises(BusinessError) as e:
        await set_returned(p6, o.id, lid, 12)
    assert e.value.code == "returned.invoice_sent"


async def test_the_rules_never_more_than_shipped_and_always_a_reason(p6):  # noqa: F811
    o = await shipped_850(p6, number="PO-3", qty=10)
    lid = await _line(p6, o.id)
    with pytest.raises(BusinessError) as e:
        await set_returned(p6, o.id, lid, 61)
    assert e.value.code == "returned.more_than_shipped"
    with pytest.raises(ValueError):  # the schema: a reason of at least three characters, never negative
        ReturnedIn(reason="", lines=[ReturnedLineIn(id=lid, returned_ea=1)])
    with pytest.raises(ValueError):
        ReturnedLineIn(id=lid, returned_ea=-1)


async def _detail(t, oid):
    async with t.factory(OP) as u:
        return await OrderService(u).detail(oid)


async def _line(t, oid):
    return (await _detail(t, oid)).lines[0].id
