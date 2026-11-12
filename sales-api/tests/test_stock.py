"""The Radiant inventory ledger (design 2026-11-12): moves from what Sales already keeps, each once."""
from datetime import date

import pytest
import sqlalchemy as sa

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.services.stock import StockItemService, StockLotService, StockMoveService, sync
from app.platform.query import QuerySpec
from tests.domain.test_transfers_edi import real_944
from tests.test_invoicing import confirm, p6, send, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer, store  # noqa: F401 — p7 is a fixture


@pytest.fixture(autouse=True)
async def _empty_ledger(db_engine):
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE sales_stock_moves RESTART IDENTITY"))


async def _sync(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        out = await sync(u)
        await u.commit()
    return out


async def _items(t):
    async with t.factory(OP) as u:
        page = await StockItemService(u).list(QuerySpec(page_size=100))
    return {r.item_code: r for r in page.items}


async def test_a_transfer_and_its_944_put_stock_at_radiant_once(p7):  # noqa: F811
    await sent_transfer(p7)  # 00104: CF0092 8400 + CF0093 7200
    await store(p7, "944_EDI", real_944(), "944-16468.edi")  # the real answer: CF0092 8400 and CF0103 7200 (not CF0093)
    await p7.tr.process_944s()
    first = await _sync(p7)
    assert first == {"transfer_line": 2, "receipt_row": 26}
    assert await _sync(p7) == {}  # a second run adds nothing
    items = await _items(p7)
    assert items["CF0092"].on_hand_ea == 8400 and items["CF0092"].to_radiant_ea == 0
    assert items["CF0103"].on_hand_ea == 7200 and items["CF0103"].to_radiant_ea == -7200  # came, never sent: shows
    assert items["CF0093"].on_hand_ea == 0 and items["CF0093"].to_radiant_ea == 7200      # sent, never came: shows
    async with p7.factory(OP) as u:
        lots = (await StockLotService(u).list(QuerySpec(page_size=100))).items
    assert {(x.item_code, x.lot): x.on_hand_ea for x in lots if x.lot} == {
        ("CF0092", "26C253125110231"): 8400, ("CF0103", "26E248121010211"): 7200}


async def test_a_945_takes_stock_out_and_a_final_delivery_splits_shipped_and_returned(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-S", qty=10)  # 10 cases shipped
    out = await _sync(p6)
    assert out.get("shipment_row", 0) >= 1 and "delivery" not in out  # not final yet: in transit to the customer
    items = await _items(p6)
    code, shipped = next((k, -v.on_hand_ea) for k, v in items.items() if v.to_customer_ea > 0)
    assert items[code].to_customer_ea == shipped
    [line] = await p6.q("SELECT id, factor_ea FROM sales_order_lines WHERE order_id = :o", o=o.id)
    case = int(line["factor_ea"])
    async with p6.factory(OP) as u:
        from app.schemas.orders import ReturnedIn
        from app.services.order_views import OrderService
        await OrderService(u).set_returned(o.id, ReturnedIn(reason="one case refused", lines=[{"id": line["id"], "returned_ea": case}]))
        await u.commit()
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    await send(p6, inv.id)
    out = await _sync(p6)
    assert out.get("delivery") and out.get("returned")
    items = await _items(p6)
    assert items[code].to_customer_ea == 0 and items[code].returning_ea == case
    async with p6.factory(OP) as u:
        moves = (await StockMoveService(u).list(QuerySpec(page_size=100))).items
    assert sum(m.qty_ea for m in moves if m.to_place == "CUSTOMER") == shipped - case


async def test_an_adjustment_needs_a_reason_and_a_quantity_and_is_a_move(p7):  # noqa: F811
    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError):
            await StockMoveService(u).adjust(item_code="CF0092", lot="L1", qty_ea=0, reason="count")
        m = await StockMoveService(u).adjust(item_code="CF0092", lot="L1", qty_ea=-12, reason="damaged at Radiant")
        await u.commit()
    assert (m.from_place, m.to_place, m.qty_ea) == ("RADIANT", "ADJUSTED", 12)
    items = await _items(p7)
    assert items["CF0092"].on_hand_ea == -12
    [n] = await p7.q("SELECT count(*) n FROM sales_stock_moves WHERE source_type = 'adjustment'")
    assert n["n"] == 1
