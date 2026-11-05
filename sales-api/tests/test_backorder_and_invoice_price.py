"""Backordered quantities and the invoice price on the order line (user
2026-11-03): a short 945 leaves ordered − shipped EA backordered on the line
and the order (FR-OB-25 still closes the fulfilment: Shipped, one task); a
shadow-run 945 and a removed line backorder nothing. The line's invoice price
defaults to the PO price, may be changed before the invoice exists (a
different price needs a reason, logged), and the draft takes it; after that
the invoice is the one place to change it."""
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.domain.lifecycle import backordered
from app.domain.status import Fulfillment as F
from app.models.orders import Order, OrderLine
from app.platform.actors import SYSTEM_ACTOR
from app.platform.query import Filter, QuerySpec, SortKey
from app.schemas.orders import InvoicePricesIn
from app.services.order_views import OrderRepository, OrderService
from tests.test_invoicing import OP, confirm, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_shipments import store_945, tasks, x945


def test_backordered_is_ordered_minus_shipped_once_shipped():
    assert backordered(60, 54, False) == 6
    assert backordered(60, 60, False) == 0
    assert backordered(60, 66, False) == 0      # over-shipped: nothing backordered
    assert backordered(60, None, False) is None  # no applied 945 yet
    assert backordered(60, 10, True) is None     # a removed line
    assert backordered(None, 10, False) is None  # no EA quantity


async def detail(t, oid):
    async with t.factory(OP) as u:
        return await OrderService(u).detail(oid)


async def test_a_partial_945_backorders_the_rest_and_the_order_shows_in_the_backordered_view(p6):  # noqa: F811
    o = await shipped_850(p6, qty=10, shipped=54)  # 10 CA = 60 EA ordered, 54 shipped
    d = await detail(p6, o.id)
    assert d.fulfillment_status == F.SHIPPED  # FR-OB-25: the short 945 still closes the fulfilment
    assert (d.lines[0].shipped_ea, d.lines[0].backordered_ea, d.lines[0].state) == (54, 6, "Short shipped")
    assert d.backordered_ea == 6
    assert len(await tasks(p6, "short_shipment")) == 1  # the existing shortfall task — no new one
    full = await shipped_850(p6, number="PO-2", qty=10)
    assert (await detail(p6, full.id)).backordered_ea == 0
    async with p6.factory(OP) as u:
        page = await OrderRepository(u).list(QuerySpec(filters=[Filter(field="backordered_ea", op="gt", value="0")]))
        assert [x.id for x in page.items] == [o.id]
        assert (await OrderRepository(u).list(QuerySpec(sort=[SortKey(field="backordered_ea", desc=True)]))).items[0].id == o.id


async def test_a_shadow_945_shows_its_figure_applied_nowhere_and_a_removed_line_backorders_nothing(p6):  # noqa: F811
    from tests.test_orders import po, run_850
    await run_850(p6.edi.env, {"PurchaseOrder": [po("LBL", "PO-S", [
        {"Line": 1, "BuyerPart": "L-555", "Quantity": 10, "UOM": "CA", "UnitPrice": "50"}], ship_to_gln="0012345000011")]})
    o, _ = await p6.edi.env.order("PO-S")
    await store_945(p6, x945(f"{o.order_number:09d}", ["LX*1", "W12*CP*60*30*0*EA**VN*CF0092"],
                             header=["DTM*011*20261102"], ctl="900000999"), "945-shadow.edi", shadow=True)
    await p6.ships.process_945s()
    d = await detail(p6, o.id)
    assert (d.lines[0].shipped_ea, d.lines[0].backordered_ea, d.backordered_ea) == (None, None, 30)  # shown, never written
    assert (d.lines[0].shipped_shadow_ea, d.lines[0].backordered_shadow_ea) == (30, 30)
    o2 = await shipped_850(p6, number="PO-R", qty=10, shipped=30)
    async with p6.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(OrderLine).where(OrderLine.order_id == o2.id))).scalar_one().removed = True
        await u.commit()
    d2 = await detail(p6, o2.id)
    assert (d2.lines[0].backordered_ea, d2.backordered_ea) == (None, None)


async def set_prices(t, oid, lines, reason=None):
    async with t.factory(OP) as u:
        out = await OrderService(u).set_invoice_prices(oid, InvoicePricesIn(reason=reason, lines=lines))
        await u.commit()
        return out


async def test_the_line_invoice_price_defaults_to_the_po_needs_a_reason_and_flows_into_the_draft(p6):  # noqa: F811
    o = await shipped_850(p6, price="50")
    d = await detail(p6, o.id)
    ln = d.lines[0]
    assert (ln.invoice_price, ln.invoice_unit_price, d.has_invoice) == (Decimal("50.00000"), None, False)  # the PO's
    with pytest.raises(Exception) as e:  # a different price without a reason is refused, nothing changes
        await set_prices(p6, o.id, [{"id": ln.id, "invoice_unit_price": "48"}])
    assert getattr(e.value, "code", "") == "invoice.reason_required"
    assert (await p6.q("SELECT invoice_unit_price p FROM sales_order_lines"))[0]["p"] is None
    out = await set_prices(p6, o.id, [{"id": ln.id, "invoice_unit_price": "48"}], reason="agreed promo")
    assert (out.lines[0].invoice_price, out.lines[0].invoice_unit_price) == (Decimal("48"), Decimal("48"))
    [a] = await p6.q("SELECT reason, before, after FROM sales_audit WHERE entity = 'sales_order_lines' "
                     "AND reason LIKE 'invoice price:%'")
    assert a["reason"] == "invoice price: agreed promo" and a["after"]["invoice_unit_price"] in ("48", "48.00000", 48)
    assert await p6.q("SELECT 1 FROM sales_log WHERE message LIKE '%invoice price line 1 CF0092 50.00000 → 48 — agreed promo%'")
    await p6.inv.advance()
    inv = await confirm(p6, o.id)
    assert inv.lines[0].unit_price == Decimal("48") and inv.lines[0].po_price == Decimal("50.00000")  # the draft took it
    assert inv.total == Decimal("480.00")
    d = await detail(p6, o.id)
    assert d.has_invoice and d.lines[0].invoice_price == Decimal("48")
    with pytest.raises(Exception) as e:  # now the invoice is the one place to change it
        await set_prices(p6, o.id, [{"id": ln.id, "invoice_unit_price": "47"}], reason="later")
    assert getattr(e.value, "code", "") == "order.invoice_exists"


async def test_back_to_the_po_price_needs_no_reason_and_another_order_line_is_refused(p6):  # noqa: F811
    o = await shipped_850(p6, price="50")
    other = await shipped_850(p6, number="PO-2", price="50")
    ln = (await detail(p6, o.id)).lines[0]
    await set_prices(p6, o.id, [{"id": ln.id, "invoice_unit_price": "52.5"}], reason="price list 2027")
    out = await set_prices(p6, o.id, [{"id": ln.id, "invoice_unit_price": "50"}])  # equal to the PO: stored as "the PO price"
    assert (out.lines[0].invoice_unit_price, out.lines[0].invoice_price) == (None, Decimal("50.00000"))
    with pytest.raises(Exception) as e:
        await set_prices(p6, o.id, [{"id": (await detail(p6, other.id)).lines[0].id, "invoice_unit_price": "1"}], reason="x y z")
    assert getattr(e.value, "code", "") == "order.line"
    async with p6.factory(SYSTEM_ACTOR) as u:
        assert (await u.session.get(Order, other.id)) is not None


async def test_the_internal_number_is_searched_exactly(p6):  # noqa: F811
    o = await shipped_850(p6)
    async with p6.factory(OP) as u:
        repo = OrderRepository(u)
        assert [x.id for x in (await repo.list(QuerySpec(search=str(o.order_number)))).items] == [o.id]
        assert (await repo.list(QuerySpec(search=str(o.order_number + 1000)))).total == 0


async def test_the_internal_number_is_beside_the_ref_on_invoices_lots_and_in_task_text(p6):  # noqa: F811
    from app.services.invoice_views import InvoiceRepository
    from app.services.lots import LotRepository
    o = await shipped_850(p6, qty=10, shipped=54)
    [t] = await tasks(p6, "short_shipment")
    assert f"Order {o.source_reference} (internal no. {o.order_number})" in t  # both, in the task's text
    await p6.inv.advance()
    await confirm(p6, o.id)
    async with p6.factory(OP) as u:
        [inv] = (await InvoiceRepository(u).list(QuerySpec(search=str(o.order_number)))).items
        assert (inv.order_ref, inv.order_number) == (o.source_reference, o.order_number)
        assert {f["name"]: f["label"] for f in InvoiceRepository.field_meta()}["order_number"] == "Internal no."
        lots = (await LotRepository(u).list(QuerySpec(search=str(o.order_number)))).items
        assert lots and {(r.order_ref, r.order_number) for r in lots} == {(o.source_reference, o.order_number)}
