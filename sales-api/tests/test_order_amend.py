"""An order sent wrong, corrected and sent again (user 2026-11-13): until the 945."""
import pytest

from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.schemas.orders import AmendOrderIn
from app.services.order_views import OrderService
from tests.test_invoicing import p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, env, po, run_850  # noqa: F401 — env is a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


async def _order(p6, number, status=None):  # noqa: F811
    await run_850(p6.edi.env, {"PurchaseOrder": [po("LBL", number, [{"Line": 1, "BuyerPart": "L-555", "Quantity": 10, "UOM": "CA",
                                                                   "UnitPrice": "50"}], ship_to_gln="0012345000011")]})
    o, _ = await p6.edi.env.order(number)
    if status:
        async with p6.factory(SYSTEM_ACTOR) as u:
            (await u.session.get(Order, o.id)).fulfillment_status = status
            await u.commit()
    return o


async def test_a_rejected_order_is_amended_and_a_new_940_goes(p6):  # noqa: F811
    o = await _order(p6, "PO-AM1", "940 Rejected")
    [ln] = await p6.q("SELECT id, item_code, qty_ea, factor_ea FROM sales_order_lines WHERE order_id = :o", o=o.id)
    async with p6.factory(OP) as u:
        out = await OrderService(u).amend(o.id, AmendOrderIn(reason="12 cases, not 10", lines=[
            {"id": ln["id"], "item_code": ln["item_code"], "ordered_qty": "12"}]), p6.edi.out)
        await u.commit()
    [after] = await p6.q("SELECT qty_ea, ordered_qty FROM sales_order_lines WHERE order_id = :o", o=o.id)
    assert after["qty_ea"] == 12 * int(ln["factor_ea"]) and float(after["ordered_qty"]) == 12
    docs = await p6.q("SELECT file_status FROM sales_documents WHERE kind = '940_EDI' AND id IN "
                      "(SELECT document_id FROM sales_document_links WHERE entity_type = 'order' AND entity_id = :o) ORDER BY id", o=o.id)
    assert out["sent"] and len(docs) == 1  # a new 940 was made (its own checks decide whether it goes)
    [log] = await p6.q("SELECT context FROM sales_log WHERE message LIKE '%amended%' AND order_id = :o", o=o.id)
    assert log["context"]["before"][0]["qty"].startswith("10") and log["context"]["after"][0]["qty"] == "12"


async def test_an_order_the_warehouse_shipped_or_a_bad_line_is_refused(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-AM2")
    [ln] = await p6.q("SELECT id, item_code FROM sales_order_lines WHERE order_id = :o", o=o.id)
    async with p6.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await OrderService(u).amend(o.id, AmendOrderIn(reason="too late", lines=[{"id": ln["id"], "item_code": ln["item_code"], "ordered_qty": "1"}]),
                                        p6.edi.out)
    assert e.value.code == "order.amend"
    o2 = await _order(p6, "PO-AM3")  # Pending: amended, it goes on the next pass
    [l2] = await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :o", o=o2.id)
    async with p6.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await OrderService(u).amend(o2.id, AmendOrderIn(reason="wrong item", lines=[{"id": l2["id"], "item_code": "NOPE", "ordered_qty": "1"}]),
                                        p6.edi.out)
        assert e.value.code == "order.amend_line"
        with pytest.raises(BusinessError) as e:
            await OrderService(u).amend(o2.id, AmendOrderIn(reason="nothing left", lines=[{"id": l2["id"], "removed": True}]), p6.edi.out)
        assert "at least one line" in e.value.message
    async with p6.factory(OP) as u:  # a fresh unit: the refused change above was never kept
        out = await OrderService(u).amend(o2.id, AmendOrderIn(reason="5 cases", lines=[{"id": l2["id"], "item_code": "CF0092", "ordered_qty": "5"}]),
                                          p6.edi.out)
        await u.commit()
    assert out["sent"] is False and out["order"]["fulfillment_status"] == "Pending"
