"""Shadow-run 945s, split shipments and tracking links (user 2026-11-03): a
shadow 945's rows are linked to the order lines (the same matching as an
applied one) without applying anything — no status, no quantity written, no
task, no Shopify write; the parts of a split shipment ("9301a", "9301b") add
up; the old shadow shipments are linked once by an idempotent backfill; the
carrier's template makes the tracking link (encoded), an unknown carrier none."""
import sqlalchemy as sa

from app.domain.carriers import tracking_url
from app.domain.status import Fulfillment as F
from app.models.shipments import ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.services.order_views import OrderService
from app.services.settings import SETTINGS, SHADOW_MODE
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_outbound import sent_order, status
from tests.test_shipments import PALLET_12, p5, store_945, writes, x945  # noqa: F401 — p5 is a fixture


async def shadow_on(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()


async def counts(t):
    one = lambda rows: rows[0]["n"]  # noqa: E731
    return {"tasks": one(await t.q("SELECT count(*) n FROM tasks")),
            "outbox": one(await t.q("SELECT count(*) n FROM sales_outbox")),
            "shipped": one(await t.q("SELECT count(*) n FROM sales_order_lines WHERE shipped_ea IS NOT NULL")),
            "invoices": one(await t.q("SELECT count(*) n FROM sales_invoices")),
            "applied": one(await t.q("SELECT count(*) n FROM sales_shipments WHERE applied"))}


async def detail(t, oid):
    async with t.factory(OP) as u:
        return await OrderService(u).detail(oid)


PART_A = ["LX*1", "MAN*GM*00077777770077782891", "W12*CP*12*7*0*EA*062639300012*VN*CF0092", "N9*LT*LOT-A", "N9*2I*EWK 0001/A"]
PART_B = ["LX*1", "MAN*GM*00077777770077782907", "W12*CP*12*5*0*EA*062639300012*VN*CF0092", "N9*LT*LOT-B"]


async def test_a_shadow_945_is_linked_to_the_lines_and_nothing_else_changes(p5):  # noqa: F811
    o, _ = await sent_order(p5.edi)
    await shadow_on(p5)
    before = await counts(p5)
    await store_945(p5, x945("9301a", PART_A, header=["W27*U*PRLA", "DTM*011*20261102"]), "945-a.edi")
    await p5.ships.process_945s()
    assert await counts(p5) == before                       # no task, no outbox, no shipped_ea, no invoice, nothing applied
    assert await status(p5.edi, o.id) == F.SENT_940         # the status stays
    assert writes(p5) == []                                 # no Shopify call
    [row] = await p5.q("SELECT order_line_id FROM sales_shipment_rows")
    assert row["order_line_id"] is not None                 # but the row is linked
    d = await detail(p5, o.id)
    ln = d.lines[0]
    assert (ln.shipped_ea, ln.backordered_ea) == (None, None)            # applied figures: untouched
    assert (ln.shipped_shadow_ea, ln.backordered_shadow_ea) == (7, 5)    # the shadow run's
    [sh] = d.shipments
    assert sh.part == "a" and sh.applied is False and sh.shadow is True


async def test_split_parts_add_up_and_backordered_goes_to_zero_after_part_b(p5):  # noqa: F811
    o, _ = await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301a", PART_A, ctl="000000781"), "945-a.edi")
    await p5.ships.process_945s()
    await store_945(p5, x945("9301b", PART_B, ctl="000000782"), "945-b.edi")
    await p5.ships.process_945s()
    d = await detail(p5, o.id)
    assert (d.lines[0].shipped_shadow_ea, d.lines[0].backordered_shadow_ea) == (12, 0)
    assert [s.part for s in d.shipments] == ["a", "b"]
    assert await status(p5.edi, o.id) == F.SENT_940 and await p5.q("SELECT 1 FROM tasks") == []  # not a "second 945"


async def test_the_backfill_links_the_old_shadow_rows_once(p5):  # noqa: F811
    o, _ = await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301a", PART_A, ctl="000000783"), "945-a.edi")
    await p5.ships.process_945s()
    await store_945(p5, x945("9301b", PART_B, ctl="000000784"), "945-b.edi")
    await p5.ships.process_945s()
    async with p5.factory(SYSTEM_ACTOR) as u:  # as stored before this round: unlinked
        await u.session.execute(sa.update(ShipmentRow).values(order_line_id=None))
        await u.commit()
    before = await counts(p5)
    async with p5.factory(SYSTEM_ACTOR) as u:
        dry = await p5.ships.link_shadow_rows(u, dry_run=True)
    assert dry == {"shipments": 2, "skipped_linked": 0, "skipped_synthetic": 0, "no_order": 0, "rows_linked": 2, "rows_unmatched": 0}
    assert await p5.q("SELECT count(*) n FROM sales_shipment_rows WHERE order_line_id IS NOT NULL") == [{"n": 0}]  # rolled back
    async with p5.factory(SYSTEM_ACTOR) as u:
        done = await p5.ships.link_shadow_rows(u, dry_run=False)
        await u.commit()
    assert done == dry
    assert await p5.q("SELECT count(*) n FROM sales_shipment_rows WHERE order_line_id IS NOT NULL") == [{"n": 2}]
    async with p5.factory(SYSTEM_ACTOR) as u:
        again = await p5.ships.link_shadow_rows(u, dry_run=False)
        await u.commit()
    assert again == {"shipments": 0, "skipped_linked": 2, "skipped_synthetic": 0, "no_order": 0, "rows_linked": 0, "rows_unmatched": 0}  # idempotent
    assert await counts(p5) == before                                                                   # linking only
    assert await p5.q("SELECT 1 FROM sales_log WHERE message LIKE 'shadow-run 945 rows linked%'")
    assert (await detail(p5, o.id)).lines[0].backordered_shadow_ea == 0


def test_the_tracking_link_comes_from_the_template_encoded_and_none_without_one():
    tpl = "https://www.purolator.com/en/shipping/tracker?pin={tracking}"
    assert tracking_url(tpl, "EWK 0001/A") == "https://www.purolator.com/en/shipping/tracker?pin=EWK%200001%2FA"
    assert tracking_url(None, "EWK1") is None and tracking_url(tpl, None) is None
    assert tracking_url("https://x.example/no-placeholder", "EWK1") is None


async def test_a_shipment_card_carries_its_carrier_links_and_rows_with_line_and_cases(p5):  # noqa: F811
    from app.models.config import Carrier
    async with p5.factory(SYSTEM_ACTOR) as u:
        if (await u.session.execute(sa.select(Carrier).where(Carrier.scac == "PRLA"))).scalar_one_or_none() is None:
            u.session.add(Carrier(scac="PRLA", name="Purolator", tracking_url="https://www.purolator.com/en/shipping/tracker?pin={tracking}",
                                  version=1, created_by_name="test"))
        await u.commit()
    o, _ = await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301", PART_A, header=["W27*U*PRLA", "N9*2I*EWK 0001/A", "DTM*011*20261102"], ctl="000000785"), "945.edi")
    await p5.ships.process_945s()
    [sh] = (await detail(p5, o.id)).shipments
    assert (sh.carrier, sh.carrier_name, sh.mode) == ("PRLA", "Purolator", "U")
    assert [(t.number, t.url) for t in sh.tracking_links] == [("EWK 0001/A", "https://www.purolator.com/en/shipping/tracker?pin=EWK%200001%2FA")]
    assert sh.document_name == "945.edi" and sh.received_at is not None and sh.part is None
    [r] = sh.rows
    assert (r.line_no, r.item_code, r.case_factor, r.lx_number) == (11, "CF0092", 6, "1")
    assert r.tracking_url == "https://www.purolator.com/en/shipping/tracker?pin=EWK%200001%2FA"
    async with p5.factory(SYSTEM_ACTOR) as u:  # an unknown carrier: the number, no link
        from app.models.shipments import Shipment
        (await u.session.execute(sa.select(Shipment))).scalar_one().carrier = "ZZZZ"
        await u.commit()
    [sh] = (await detail(p5, o.id)).shipments
    assert sh.carrier_name is None and [t.url for t in sh.tracking_links] == [None] and sh.rows[0].tracking_url is None


async def test_the_backfill_leaves_a_synthetic_test_945_alone(p5):  # noqa: F811
    await sent_order(p5.edi)
    await shadow_on(p5)
    await store_945(p5, x945("9301", PALLET_12, ctl="000000786"), "SYNTHETIC-945-order9301.edi")
    await p5.ships.process_945s()
    async with p5.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.update(ShipmentRow).values(order_line_id=None))
        await u.commit()
    async with p5.factory(SYSTEM_ACTOR) as u:
        done = await p5.ships.link_shadow_rows(u, dry_run=False)
        await u.commit()
    assert done["skipped_synthetic"] == 1 and done["shipments"] == 0 and done["rows_linked"] == 0
    assert await p5.q("SELECT count(*) n FROM sales_shipment_rows WHERE order_line_id IS NOT NULL") == [{"n": 0}]
