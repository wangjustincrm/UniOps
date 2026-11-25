"""What changes of a record's parts is in the audit (D, 2026-11-25: record more, never less): a transfer's lines as an order's — made,
changed, deleted; a 945's rows and a count's lines, whose making is the file that is kept, when they change after it."""
import pytest
import sqlalchemy as sa

from app.models.orders import Order
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import StockCount, StockCountLine
from app.platform.actors import SYSTEM_ACTOR
from app.platform.entities import Audited
from app.schemas.transfers import TransferIn
from app.services.order_holds import OrderActions
from app.services.record_files import timeline_of
from app.services.transfer_views import TransferService
from tests.domain.test_transfers_edi import real_944
from tests.test_lock_order_shopify import p5, to_decide  # noqa: F401 — p5 is a fixture
from tests.test_order_from_945 import a_945, make
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_parts import MODELS, PARTS
from tests.test_shipments import store_945, x945
from tests.test_transfers import create, p7, sent_transfer, status, store  # noqa: F401 — p7 is a fixture


async def trail(q, entity, *more):
    rows = await q("SELECT action, entity_id, before, after, user_name, reason FROM sales_audit WHERE entity = :e ORDER BY id", e=entity)
    return rows[len(more) and more[0]:]


def test_every_part_leaves_a_row_when_it_changes():
    for name, _ in PARTS:
        cls = next(c for c in MODELS.values() if c.__name__ == name)
        assert issubclass(cls, Audited) or "update" in getattr(cls, "__audited_rows__", ()) or getattr(cls, "__audited_fields__", None), name
        if "__audited_rows__" in cls.__dict__:
            assert not issubclass(cls, Audited) and cls.__audited_rows__ <= {"create", "update", "delete"} and "delete" in cls.__audited_rows__


async def test_a_transfer_s_lines_made_changed_and_deleted(p7):  # noqa: F811
    x = await create(p7, (("CF0092", 8400), ("CF0093", 7200)))
    made = await trail(p7.q, "sales_transfer_lines")
    assert [(r["action"], r["before"], r["after"]["item_code"], r["after"]["qty_ea"], r["after"]["transfer_id"], r["user_name"]) for r in made] == [
        ("create", None, "CF0092", 8400, x.id, OP.name), ("create", None, "CF0093", 7200, x.id, OP.name)]
    async with p7.factory(OP) as u:
        await TransferService(u).update(p7.tr, x.id, TransferIn(from_warehouse_id=x.from_warehouse_id, to_warehouse_id=x.to_warehouse_id,
                                        lines=[{"item_code": "CF0092", "qty_ea": 600}]), x.version)
        await u.commit()
    edited = await trail(p7.q, "sales_transfer_lines", 2)
    assert sorted((r["action"], (r["before"] or r["after"])["item_code"], (r["before"] or r["after"])["qty_ea"], r["reason"]) for r in edited) == [
        ("create", "CF0092", 600, "transfer edited"), ("delete", "CF0092", 8400, "transfer edited"), ("delete", "CF0093", 7200, "transfer edited")]
    async with p7.factory(OP) as u:
        shown = await timeline_of(u.session, "transfer", x.id)
    assert ("sales_transfer_lines", "create", 600) in [(e.entity, e.action, (e.after or {}).get("qty_ea")) for e in shown]


async def test_what_a_944_received_of_a_transfer(p7):  # noqa: F811
    x = await sent_transfer(p7, (("CF0092", 8400), ("CF0103", 7200)))
    n = len(await trail(p7.q, "sales_transfer_lines"))
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    await p7.tr.process_944s()
    assert await status(p7, x.id) == "Received"
    got = await trail(p7.q, "sales_transfer_lines", n)
    assert sorted((r["action"], r["before"]["received_ea"], r["after"]["received_ea"], r["user_name"]) for r in got) == [
        ("update", 0, 7200, SYSTEM_ACTOR.name), ("update", 0, 8400, SYSTEM_ACTOR.name)]


async def test_a_945_s_rows_leave_none_when_made_and_one_when_decided(p5):  # noqa: F811
    oid, sid, rid = await to_decide(p5)
    assert await trail(p5.q, "sales_shipment_rows") == []  # two rows came in: the file is kept, not twice
    async with p5.factory(OP) as u:
        await OrderActions(u, p5.edi.env.tasks).resolve_row(sid, rid, None, "a sample, accepted")
        await u.commit()
    [r] = await trail(p5.q, "sales_shipment_rows")
    assert (r["action"], r["entity_id"], r["before"], r["after"], r["user_name"]) == ("update", str(rid), {"needs_review": True}, {"needs_review": False}, OP.name)
    assert "a sample, accepted" in r["reason"]


async def test_the_rows_of_a_945_that_joins_an_order_are_part_of_its_making(p5):  # noqa: F811
    await make(p5, await a_945(p5, key="001008561a", rows=["LX*1", "W12*CC*12*7*0*EA**VN*CF0092"]))
    linked = len(await trail(p5.q, "sales_shipment_rows"))
    assert linked == 1  # made with no order, linked later by a person: that is a change
    await store_945(p5, x945("001008561b", ["LX*1", "W12*CC*12*5*0*EA**VN*CF0092"], [], ctl="000000778"), "b.edi")
    await p5.ships.process_945s()
    assert await p5.q("SELECT 1 FROM sales_shipment_rows WHERE order_line_id IS NULL") == []
    assert len(await trail(p5.q, "sales_shipment_rows")) == linked


async def test_a_count_s_lines_leave_none_when_uploaded_and_one_each_when_changed_or_deleted(env):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from app.services.stock_counts import StockCountService
    from tests.test_stock_counts import SUMMARY
    async with env.factory(OP) as u:
        c = await StockCountService(u).create(name="i.csv", data=SUMMARY.encode(), counted_at=datetime.now(timezone.utc) - timedelta(hours=1), note=None)
        await u.commit()
    assert await trail(env.q, "sales_stock_count_lines") == []
    async with env.factory(OP) as u:
        u.set_reason("looked again")
        await u.session.get(StockCount, c.id, with_for_update=True)
        a, b = (await u.session.execute(sa.select(StockCountLine).where(StockCountLine.count_id == c.id).order_by(StockCountLine.id).limit(2))).scalars()
        was, a.qty_ea = a.qty_ea, a.qty_ea + 1
        await u.session.delete(b)
        await u.commit()
    rows = await trail(env.q, "sales_stock_count_lines")
    assert sorted((r["action"], r["reason"]) for r in rows) == [("delete", "looked again"), ("update", "looked again")]
    assert next(r for r in rows if r["action"] == "update")["before"] == {"qty_ea": was}


@pytest.mark.parametrize("model", [ShipmentRow])
async def test_a_row_is_not_made_twice_over_flushes_of_one_transaction(p5, model):  # noqa: F811
    oid, sid, _ = await to_decide(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.get(Shipment, sid, with_for_update=True)
        await u.session.get(Order, oid)
        r = model(shipment_id=sid, lx_index=90, w12_index=1, qty_shipped_ea=1, needs_review=False)
        u.session.add(r)
        await u.session.flush()
        r.qty_shipped_ea = 2
        await u.session.flush()
        await u.session.delete(r)
        await u.commit()
    assert await trail(p5.q, "sales_shipment_rows") == []


async def test_the_version_the_activity_and_the_audit_agree(p7):  # noqa: F811
    x = await create(p7, (("CF0092", 600),))
    async with p7.factory(OP) as u:
        out = await TransferService(u).update(p7.tr, x.id, TransferIn(from_warehouse_id=x.from_warehouse_id, to_warehouse_id=x.to_warehouse_id,
                                              lines=[{"item_code": "CF0092", "qty_ea": 700}]), x.version)
        await u.commit()
    [now] = await p7.q("SELECT version FROM sales_transfers WHERE id = :i", i=x.id)
    assert out.version == now["version"] > x.version  # read after the last flush (the old lines, then the new): what the next save sends
    assert await p7.q("SELECT 1 FROM sales_audit WHERE entity = 'sales_transfers' AND action = 'update'") == []  # no empty row of the root
