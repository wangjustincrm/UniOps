"""Restart from a date (user 2026-11-05): older orders go with what hangs from
them, files stay, a returning Shopify order is Historical, and it refuses where
it must. Order date decides, except an 850 that reached us on the day."""
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa

from app.models.config import Connection
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.start_orders_from import Refused, lowest_number, run
from app.services.settings import SETTINGS, SHADOW_MODE
from tests.test_orders import env  # noqa: F401 — a fixture

START = date(2026, 11, 4)
OLD, NEW = datetime(2026, 11, 2, tzinfo=timezone.utc), datetime(2026, 11, 4, 9, tzinfo=timezone.utc)


async def seed(t, *, shadow=True):
    async with t.factory(SYSTEM_ACTOR) as u:
        s = u.session
        await SETTINGS.set(s, SHADOW_MODE, shadow)
        conn = (await s.execute(sa.select(Connection).where(Connection.kind == "SHOPIFY"))).scalars().first()
        conn.order_number_cutoff = 9000

        def order(src, sid, day, received, number=None, cid=None):
            return Order(partner_code="SHOP", source_type=src, source_order_id=sid, source_order_number=number, connection_id=cid,
                         order_date=day, first_received_at=received, fulfillment_status="Pending", invoice_status="Not Required", version=1)
        old = order("SHOPIFY", "old", date(2026, 11, 3), OLD, "9540", conn.id)
        new = order("SHOPIFY", "new", START, NEW, "9541", conn.id)
        late_850 = order("EDI_850", "late", date(2026, 11, 3), NEW)
        old_850 = order("EDI_850", "early", date(2026, 11, 3), OLD)
        s.add_all([old, new, late_850, old_850])
        doc = Document(direction="IN", origin="PARTNER", system="RADIANT", kind="945_EDI", size=1, content=b"x", sha256="a" * 64,
                       processing_status="Processed")
        s.add(doc)
        await s.flush()
        s.add(OrderLine(order_id=old.id, line_no=1, source={}, ordered_qty=1))
        s.add(Shipment(order_id=old.id, document_id=doc.id, set_index=0, applied=True, shadow=True, shopify_status="Not needed"))
        s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=old.id))
        await u.commit()
    return old.id, doc.id


async def left(t):
    return sorted(r["source_order_id"] for r in await t.q("SELECT source_order_id FROM sales_orders"))


async def test_a_dry_run_keeps_everything(env):  # noqa: F811
    await seed(env)
    r = await run(env.factory, START, apply=False)
    assert r["deleted"]["sales_orders"] == 2 and r["orders_left"] == 2
    assert await left(env) == ["early", "late", "new", "old"]
    assert (await env.q("SELECT order_number_cutoff FROM sales_connections WHERE kind = 'SHOPIFY'"))[0]["order_number_cutoff"] == 9000


async def test_apply_deletes_older_orders_and_keeps_the_files(env):  # noqa: F811
    _, doc = await seed(env)
    await run(env.factory, START, apply=True)
    assert await left(env) == ["late", "new"]
    assert await env.q("SELECT 1 FROM sales_shipments") == [] and await env.q("SELECT 1 FROM sales_order_lines") == []
    assert await env.q("SELECT 1 FROM sales_document_links") == []
    assert await env.q(f"SELECT 1 FROM sales_documents WHERE id = {doc}") != []  # not downloaded twice
    assert (await env.q("SELECT order_number_cutoff FROM sales_connections WHERE kind = 'SHOPIFY'"))[0]["order_number_cutoff"] == 9541
    assert len(await env.q("SELECT 1 FROM sales_log WHERE step = 'restart'")) == 1
    again = await run(env.factory, START, apply=True)
    assert again["deleted"]["sales_orders"] == 0 and again["cutoff"] == {}


async def test_refused_outside_the_shadow_run(env):  # noqa: F811
    await seed(env, shadow=False)
    with pytest.raises(Refused):
        await run(env.factory, START, apply=True)
    assert len(await left(env)) == 4


def test_lowest_number_reads_shopify_names():
    assert lowest_number(["#9541", "9553", None, "SHP-1"]) == 9541
    assert lowest_number([None]) is None
