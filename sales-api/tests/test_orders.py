"""P3 acceptance: orders from Shopify and retailer 850s, units, payment hold,
the delay clock, archive rules, review and manual orders — AC-OB-10, 12…20,
22, 23, 70, 74, 79 as far as P3 reaches (the 940 itself is P4: there the
"no 940 is sent" halves become sends). Shopify is the in-memory fake; the
HTTP client is tested against httpx.MockTransport in test_shopify_gateway."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.order_rules import OrderFacts, PartnerFacts, qualifies
from app.domain.status import Fulfillment as F
from app.integrations.epms_tasks import shared_tasks_table
from app.integrations.shopify import FakeShopify
from app.models.config import Connection, DcRouting, Item, ItemPartnerCode, ItemUnit, Partner
from app.models.documents import Document
from app.models.orders import Order, OrderLine
from app.models.records import AuditRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.audit import AuditTrail
from app.platform.uow import UnitOfWorkFactory
from app.services.edi_intake import EdiIntake
from app.services.intake import ConnectionSnapshot
from app.services.order_views import ManualOrderService, OrderService
from app.services.problems import task_service
from app.services.shopify_intake import ShopifyIntake

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
OP = SimpleNamespace(user_id=uuid.uuid4(), name="Op", role="sales_operator", is_system=False)


async def allow_all(session, actor, key):  # noqa: ARG001
    return True


@pytest_asyncio.fixture
async def env(db_engine):
    async with db_engine.begin() as c:
        await c.run_sync(lambda sc: shared_tasks_table(sa.MetaData()).metadata.create_all(sc, checkfirst=True))
        await c.execute(sa.text(
            "TRUNCATE sales_order_lines, sales_orders, sales_log, sales_document_links, sales_documents, sales_runs, "
            "sales_item_partner_codes, sales_dc_routing, sales_item_units, sales_items, sales_connections, "
            "sales_partners, sales_audit RESTART IDENTITY CASCADE"))
        await c.execute(sa.text("DELETE FROM tasks"))
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=allow_all, audit=AuditTrail(AuditRecord))
    async with factory(SYSTEM_ACTOR) as u:
        s = u.session
        for code, name, channel, settle, delay, method, dc in (
                ("SHOP", "Shopify", "SHOPIFY", "PREPAID", 180, "NONE", False),
                ("WMT", "Walmart", "TRUECOMMERCE", "INVOICE", 60, "810_JSON", True),
                ("LBL", "Loblaws", "TRUECOMMERCE", "INVOICE", 0, "810_JSON", False),
                ("SOB", "Sobeys", "TRUECOMMERCE", "INVOICE", 0, "810_JSON", False),
                ("MAN", "Manual", "MANUAL", "PREPAID", 0, "NONE", False)):
            s.add(Partner(code=code, name=name, abbrev=code, channel=channel, settlement_mode=settle,
                          sending_mode="AUTOMATIC", delay_minutes=delay, invoice_method=method,
                          dc_routing_required=dc, version=1))
        await s.flush()
        for code, sku, gtin, upc, units in (
                ("CF0092", "CF0092-SKU", "10062639300019", "062639300012", {"EA": 1, "CASE": 6, "2PACK": 2}),
                ("CF0010", "CF0010-SKU", None, "062639300029", {"EA": 1, "CASE": 12}),
                ("CRMMARK002", "CRMMARK002", None, None, {"EA": 1})):  # a free bib: EA only
            s.add(Item(item_code=code, sku=sku, gtin=gtin, upc=upc, warehouse_held=True, version=1))
            await s.flush()
            for unit, f in units.items():
                s.add(ItemUnit(item_code=code, unit=unit, factor_ea=Decimal(f), version=1))
        # AC-OB-12: the same item under three partners' own buyer parts
        for partner, part in (("LBL", "L-555"), ("SOB", "S-777"), ("WMT", "W-999")):
            s.add(ItemPartnerCode(partner_code=partner, buyer_part=part, item_code="CF0092", version=1))
        s.add(DcRouting(partner_code="WMT", ship_to_gln="0012345000011", dc_number="6080", version=1))
        conn = Connection(name="store", kind="SHOPIFY", host="example.myshopify.com", after_read="LEAVE", mode="TEST",
                          secret_name="SALES_SECRET_SHOPIFY", api_version="2026-04", page_size=2,
                          order_number_cutoff=9000, partner_code="SHOP", cursor_updated_at=T0 - timedelta(days=1),
                          version=1)
        s.add(conn)
        await s.flush()
        await u.commit()
    tasks = task_service(lambda: False)
    shop = FakeShopify()
    clock = SimpleNamespace(now=T0)
    intake = ShopifyIntake(uow_factory=factory, shopify=shop, tasks=tasks, clock=lambda: clock.now)

    async def snap():
        async with factory(SYSTEM_ACTOR) as u:
            return ConnectionSnapshot.of(await u.session.get(Connection, conn.id))

    async def order(source_id):
        async with factory(SYSTEM_ACTOR) as u:
            o = (await u.session.execute(sa.select(Order).where(Order.source_order_id == str(source_id)))).scalar_one()
            lines = list((await u.session.execute(sa.select(OrderLine).where(OrderLine.order_id == o.id)
                                                  .order_by(OrderLine.line_no))).scalars())
            return o, lines

    async def q(sql, **p):
        async with db_engine.connect() as c:
            return (await c.execute(sa.text(sql), p)).mappings().all()

    async def pull(at=None):
        if at:
            clock.now = at
        return await intake.run_pass(await snap())

    return SimpleNamespace(factory=factory, tasks=tasks, shop=shop, clock=clock, intake=intake, snap=snap,
                           order=order, q=q, pull=pull, conn_id=conn.id)


def shopify_order(oid=1001, number=9275, *, updated=T0, financial="paid", lines=None, closed_at=None,
                  cancelled_at=None, refunds=None):
    return {
        "id": oid, "name": f"#{number}", "order_number": number, "created_at": "2026-10-29T08:00:00-04:00",
        "updated_at": updated.isoformat(), "financial_status": financial, "fulfillment_status": None,
        "currency": "CAD", "total_price": "100.00", "subtotal_price": "90.00", "total_tax": "10.00",
        "email": "a@b.example", "closed_at": closed_at, "cancelled_at": cancelled_at, "refunds": refunds or [],
        "shipping_address": {"name": "A B", "address1": "1 rue", "city": "Montréal", "province_code": "QC",
                             "country_code": "CA", "zip": "H2X 1Y4"},
        "line_items": lines if lines is not None else [
            {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 2,
             "price": "50.00", "requires_shipping": True}],
    }


def facts(o: Order, settlement="PREPAID", sending="AUTOMATIC", delay=180):
    return (OrderFacts(o.fulfillment_status, o.invoice_status, o.source_type, o.shopify_financial_status,
                       o.shopify_fulfillment_status, bool(o.archived_in_shopify_at), o.historical,
                       o.first_received_at, o.paid_at),
            PartnerFacts(settlement, sending, delay))


# ── AC-OB-10: the cursor ─────────────────────────────────────────────────────

async def test_ac10_the_boundary_order_returns_and_is_recognised(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0), shopify_order(2, 9302, updated=T0 + timedelta(minutes=5))]
    r = await env.pull()
    assert (r.orders, r.created, r.status) == (2, 2, "ok")
    assert (await env.snap()).cursor_updated_at == T0 + timedelta(minutes=5)  # the batch maximum
    r = await env.pull()  # the boundary order (updated_at == cursor) comes back
    assert (r.orders, r.unchanged, r.created, r.updated) == (1, 1, 0, 0)
    assert len(await env.q("SELECT 1 FROM sales_orders")) == 2
    assert all(c[1]["page_info"] or c[1]["updated_at_min"] for c in env.shop.calls)


async def test_ac10_a_failure_part_way_leaves_the_cursor(env):
    before = (await env.snap()).cursor_updated_at
    env.shop.orders = [shopify_order(1, 9301, updated=T0), {"name": "#9302", "updated_at": T0.isoformat()},
                       shopify_order(3, 9303, updated=T0 + timedelta(minutes=9))]
    r = await env.pull()
    assert r.failed == 1 and r.status == "not_advanced"
    assert (await env.snap()).cursor_updated_at == before  # unchanged
    assert {t["type"] for t in await env.q("SELECT type FROM tasks")} == {"sales_shopify_fetch_failed"}


async def test_an_empty_cursor_pulls_nothing(env):
    async with env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_connections SET cursor_updated_at = NULL"))
        await u.commit()
    env.shop.orders = [shopify_order()]
    r = await env.pull()
    assert r.status == "cursor_not_set" and env.shop.calls == []


# ── AC-OB-16 / 20 / 74 / 17: upsert, payment hold, the delay clock ──────────

async def test_ac16_ac20_ac74_unpaid_then_paid_two_days_later(env):
    env.shop.orders = [shopify_order(1, 9301, financial="pending", updated=T0)]
    await env.pull(T0)
    o, _ = await env.order(1)
    assert (o.fulfillment_status, o.hold_reason, o.paid_at) == (F.PAYMENT_HOLD, "Payment Required", None)
    assert not qualifies(*facts(o), now=T0 + timedelta(days=9))[0]

    paid_at = T0 + timedelta(days=2)
    env.shop.orders = [shopify_order(1, 9301, financial="paid", updated=paid_at)]
    await env.pull(paid_at)
    o, _ = await env.order(1)
    assert (o.fulfillment_status, o.paid_at) == (F.PENDING, paid_at)  # released; the delay starts here
    assert len(await env.q("SELECT 1 FROM sales_orders")) == 1  # AC-OB-16: updated, never a second order
    assert qualifies(*facts(o), now=paid_at + timedelta(minutes=179))[1] == "delay not elapsed"
    assert qualifies(*facts(o), now=paid_at + timedelta(minutes=180))[0]  # AC-OB-74: from payment, not receipt

    # AC-OB-17: an update two hours into the delay does not restart it
    env.shop.orders = [shopify_order(1, 9301, financial="paid", updated=paid_at + timedelta(hours=2),
                                     lines=[{"id": 11, "sku": "CF0092-SKU", "variant_title": "Case", "quantity": 3,
                                             "price": "50", "requires_shipping": True}])]
    await env.pull(paid_at + timedelta(hours=2))
    o, lines = await env.order(1)
    assert o.paid_at == paid_at and lines[0].qty_ea == 18
    assert qualifies(*facts(o), now=paid_at + timedelta(minutes=180))[0]


# ── AC-OB-22: payment by settlement mode ─────────────────────────────────────

@pytest.mark.parametrize("settlement, financial, ok", [
    ("INVOICE", None, True), ("PREPAID", "pending", False), ("PREPAID", "authorized", False),
    ("PREPAID", "partially_paid", False), ("PREPAID", "partially_refunded", True), ("PREPAID", "refunded", False),
    ("PREPAID", "voided", False), ("PREPAID", "paid", True)])
def test_ac22_payment_satisfied(settlement, financial, ok):
    o = OrderFacts(F.PENDING, "Pending", "EDI_850" if settlement == "INVOICE" else "SHOPIFY", financial, None,
                   False, False, T0, T0)
    assert qualifies(o, PartnerFacts(settlement, "AUTOMATIC", 0), now=T0)[0] is ok


def test_qualifies_needs_every_condition():
    base = OrderFacts(F.PENDING, "Pending", "SHOPIFY", "paid", None, False, False, T0, T0)
    p = PartnerFacts("PREPAID", "AUTOMATIC", 0)
    assert qualifies(base, p, T0) == (True, "")
    from dataclasses import replace
    for change, why in ((dict(archived=True), "archived in Shopify"), (dict(historical=True), "historical order"),
                        (dict(shopify_fulfillment_status="fulfilled"), "already fulfilled"),
                        (dict(fulfillment_status=F.DRAFT), "order state is not Active"),
                        (dict(fulfillment_status=F.REVIEW), "order state is not Active")):
        assert qualifies(replace(base, **change), p, T0) == (False, why)
    assert qualifies(base, PartnerFacts("PREPAID", "MANUAL", 0), T0) == (False, "partner sends manually")


# ── AC-OB-23: archive ────────────────────────────────────────────────────────

async def test_ac23_archived_before_any_940_is_suppressed_with_one_task_and_unarchive_restores(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0)]
    await env.pull(T0)
    env.shop.orders = [shopify_order(1, 9301, updated=T0 + timedelta(minutes=30), closed_at=(T0 + timedelta(minutes=30)).isoformat())]
    await env.pull(T0 + timedelta(minutes=30))
    o, _ = await env.order(1)
    assert (o.fulfillment_status, o.archive_suppressed) == (F.ARCHIVE_SUPPRESSED, True)
    assert not qualifies(*facts(o), now=T0 + timedelta(days=1))[0]
    [task] = await env.q("SELECT type, is_completed, description FROM tasks")
    assert task["type"] == "sales_order_archived" and not task["is_completed"] and "9301" in task["description"]

    env.shop.orders = [shopify_order(1, 9301, updated=T0 + timedelta(hours=1))]  # un-archived
    await env.pull(T0 + timedelta(hours=1))
    o, _ = await env.order(1)
    assert (o.fulfillment_status, o.archive_suppressed) == (F.PENDING, False)
    [task] = await env.q("SELECT is_completed FROM tasks")
    assert task["is_completed"] is True  # closed automatically


async def test_ac23_archiving_after_fulfilment_is_ignored_and_refunded_raises_no_task(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0), shopify_order(2, 9302, updated=T0, financial="refunded")]
    await env.pull(T0)
    async with env.factory(SYSTEM_ACTOR) as u:  # P5 sets Shipped through the 945; here directly
        await u.session.execute(sa.text("UPDATE sales_orders SET fulfillment_status = 'Shipped' WHERE source_order_id = '1'"))
        await u.commit()
    later = T0 + timedelta(hours=1)
    env.shop.orders = [shopify_order(1, 9301, updated=later, closed_at=later.isoformat()),
                       shopify_order(2, 9302, updated=later, financial="refunded", closed_at=later.isoformat())]
    await env.pull(later)
    shipped, _ = await env.order(1)
    refunded, _ = await env.order(2)
    assert (shipped.fulfillment_status, shipped.archive_suppressed) == (F.SHIPPED, False)  # ignored
    # refunded, so never paid: it stays in Payment Hold, the suppression is recorded, no task
    assert (refunded.fulfillment_status, refunded.archive_suppressed) == (F.PAYMENT_HOLD, True)
    assert await env.q("SELECT 1 FROM tasks") == []


# ── AC-OB-79 / 70: units and lines to ship ──────────────────────────────────

async def test_ac79_shopify_units_come_from_the_item_master(env):
    env.shop.orders = [shopify_order(1, 9301, lines=[
        {"id": 1, "sku": "CF0092-SKU", "variant_title": "2-Pack", "quantity": 3, "price": "20", "requires_shipping": True},
        {"id": 2, "sku": "CF0092-SKU", "variant_title": "Case", "quantity": 2, "price": "50", "requires_shipping": True},
        {"id": 3, "sku": "CF0092-SKU", "variant_title": "Default Title", "quantity": 4, "price": "9", "requires_shipping": True},
    ])]
    await env.pull()
    o, lines = await env.order(1)
    assert [(ln.order_unit, ln.qty_ea) for ln in lines] == [("2PACK", 6), ("CASE", 12), ("EA", 4)]
    assert o.fulfillment_status == F.PENDING


@pytest.mark.parametrize("variant, sku, reason", [
    ("Box of 24", "CF0092-SKU", "looks like a pack size"),   # a pack no unit of the item matches
    ("2-Can", "CF0092-SKU", "looks like a pack size"),       # 2PACK without a "2-Can" Shopify title
    ("Case", "NOPE", "item not in the Item Master"),
])
async def test_ac79_ac13_what_cannot_be_converted_goes_to_review_with_the_identifiers(env, variant, sku, reason):
    quantity = 1
    env.shop.orders = [shopify_order(1, 9301, lines=[{"id": 1, "sku": sku, "variant_title": variant,
                                                      "quantity": quantity, "price": "1", "requires_shipping": True}])]
    await env.pull()
    o, lines = await env.order(1)
    assert o.fulfillment_status == F.REVIEW and reason in o.review_reason and sku in o.review_reason
    assert lines[0].qty_ea is None and reason in lines[0].problem
    assert not qualifies(*facts(o), now=T0 + timedelta(days=9))[0]  # no 940 until resolved
    [task] = await env.q("SELECT type, description FROM tasks")
    assert task["type"] == "sales_order_review" and sku in task["description"] and variant in task["description"]


async def test_ac79_a_title_naming_no_pack_is_ea(env):
    env.shop.orders = [shopify_order(1, 9301, lines=[
        {"id": 1, "sku": "CRMMARK002", "variant_title": "Bib", "quantity": 2, "price": "0", "requires_shipping": True},
        {"id": 2, "sku": "CF0092-SKU", "variant_title": "Bib", "quantity": 1, "price": "0", "requires_shipping": True},
        {"id": 3, "sku": "CF0092-SKU", "variant_title": "Can", "quantity": 3, "price": "9", "requires_shipping": True},
    ])]
    await env.pull()
    o, lines = await env.order(1)
    assert [(ln.order_unit, ln.qty_ea, ln.problem) for ln in lines] == [("EA", 2, None), ("EA", 1, None), ("EA", 3, None)]
    assert o.fulfillment_status == F.PENDING


async def test_ac79_an_ea_only_item_titled_as_a_case_goes_to_review(env):
    env.shop.orders = [shopify_order(1, 9301, lines=[
        {"id": 1, "sku": "CRMMARK002", "variant_title": "Case (6 Cans)", "quantity": 1, "price": "0",
         "requires_shipping": True}])]
    await env.pull()
    o, [line] = await env.order(1)
    assert o.fulfillment_status == F.REVIEW and line.qty_ea is None and "looks like a pack size" in line.problem


async def test_ac79_shopify_titles_on_a_unit_name_it(env):
    async with env.factory(SYSTEM_ACTOR) as u:
        unit = (await u.session.execute(sa.select(ItemUnit).where(
            ItemUnit.item_code == "CF0092", ItemUnit.unit == "2PACK"))).scalar_one()
        unit.shopify_titles = "2-Can, Twin pack"
        await u.commit()
    env.shop.orders = [shopify_order(1, 9301, lines=[
        {"id": 1, "sku": "CF0092-SKU", "variant_title": "2-Can", "quantity": 3, "price": "20", "requires_shipping": True},
        {"id": 2, "sku": "CF0092-SKU", "variant_title": "Case (6 Cans)", "quantity": 1, "price": "50",
         "requires_shipping": True},
    ])]
    await env.pull()
    o, lines = await env.order(1)
    assert [(ln.order_unit, ln.qty_ea) for ln in lines] == [("2PACK", 6), ("CASE", 6)]
    assert o.fulfillment_status == F.PENDING


# ── FR-OB-02 / A.11 (product owner, 2026-10-30): SKU, then barcode as GTIN, then UPC ──

def line(lid, sku, variant_id, title="Can", qty=1):
    return {"id": lid, "sku": sku, "variant_id": variant_id, "variant_title": title, "quantity": qty, "price": "1",
            "requires_shipping": True}


async def test_a_sku_miss_matches_the_variant_barcode_as_gtin_then_upc_in_any_form(env):
    env.shop.variants = {"501": {"barcode": "62639300012"},      # CF0092's UPC without its leading zero
                         "502": {"barcode": "0062639300029"},    # CF0010's UPC as EAN-13
                         "503": {"barcode": "10062639300019"}}   # CF0092's GTIN-14 (a case-level code)
    env.shop.orders = [shopify_order(1, 9301, lines=[
        line(1, "OLD-SKU", 501), line(2, None, 502), line(3, "OLD-SKU", 503),
        line(4, "OLD-SKU", 501, qty=2),                           # the same variant: read once
        line(5, "CF0092-SKU", 509)])]                            # the SKU matches: no read at all
    await env.pull()
    o, lines = await env.order(1)
    assert [(ln.item_code, ln.qty_ea) for ln in lines] == [
        ("CF0092", 1), ("CF0010", 1), ("CF0092", 1), ("CF0092", 2), ("CF0092", 1)]
    assert o.fulfillment_status == F.PENDING and lines[0].source["barcode"] == "62639300012"
    assert [c[1]["variant_id"] for c in env.shop.calls if c[0] == "variant"] == ["501", "502", "503"]


async def test_the_item_code_is_no_longer_a_shopify_match_and_a_miss_names_sku_and_barcode(env):
    env.shop.variants = {"601": {"barcode": None}, "602": {"barcode": "999999999993"}}
    env.shop.orders = [shopify_order(1, 9301, lines=[line(1, "CF0010", 601), line(2, "X-1", 602)])]
    await env.pull()
    o, lines = await env.order(1)
    assert o.fulfillment_status == F.REVIEW and [ln.item_code for ln in lines] == [None, None]
    assert [ln.problem for ln in lines] == ["item not in the Item Master"] * 2
    assert "sku=CF0010" in o.review_reason and "sku=X-1" in o.review_reason and "barcode=999999999993" in o.review_reason


async def test_a_barcode_read_that_fails_goes_to_review_and_a_re_check_reads_it_again(env):
    env.shop.fail["variant"] = httpx.ConnectError("down")
    env.shop.orders = [shopify_order(1, 9301, lines=[line(1, "OLD-SKU", 701)])]
    await env.pull()
    o, [ln] = await env.order(1)
    assert o.fulfillment_status == F.REVIEW and ln.item_code is None  # never guessed
    assert ln.problem == "the variant's barcode could not be read from Shopify: The store could not be reached."
    del env.shop.fail["variant"]
    env.shop.variants = {"701": {"barcode": "062639300029"}}
    async with env.factory(OP) as u:
        r = await OrderService(u).resolve_review(o.id, env.tasks, env.shop)
        await u.commit()
    assert r.remaining == [] and r.order.fulfillment_status == F.PENDING
    _, [ln] = await env.order(1)
    assert (ln.item_code, ln.source.get("barcode"), "barcode_error" in ln.source) == ("CF0010", "062639300029", False)


async def test_a_barcode_on_two_items_is_not_a_match(env):
    async with env.factory(SYSTEM_ACTOR) as u:
        u.session.add(Item(item_code="CF0011", sku="CF0011-SKU", upc="62639300029", warehouse_held=True, version=1))
        await u.session.flush()
        u.session.add(ItemUnit(item_code="CF0011", unit="EA", factor_ea=1, version=1))
        await u.commit()
    env.shop.variants = {"801": {"barcode": "062639300029"}}
    env.shop.orders = [shopify_order(1, 9301, lines=[line(1, "OLD-SKU", 801)])]
    await env.pull()
    o, [ln] = await env.order(1)
    assert o.fulfillment_status == F.REVIEW and ln.problem == "barcode matches several items (CF0010, CF0011)"


async def test_ac70_only_the_remaining_shippable_quantity(env):
    env.shop.orders = [shopify_order(1, 9301, financial="partially_refunded", refunds=[
        {"refund_line_items": [{"line_item_id": 1, "quantity": 1}]}], lines=[
        {"id": 1, "sku": "CF0092-SKU", "variant_title": "Case", "quantity": 3, "price": "50", "requires_shipping": True},
        {"id": 2, "sku": None, "title": "Gift card", "quantity": 1, "price": "25", "gift_card": True},
        {"id": 3, "sku": "CF0092-SKU", "title": "E-book", "quantity": 1, "price": "5", "requires_shipping": False},
        {"id": 4, "sku": "CF0092-SKU", "variant_title": "Case", "quantity": 1, "current_quantity": 0, "price": "50",
         "requires_shipping": True},
    ])]
    await env.pull()
    o, lines = await env.order(1)
    assert [(ln.line_no, ln.qty_ea, ln.removed) for ln in lines] == [(1, 12, False), (2, None, True), (3, None, True),
                                                                      (4, None, True)]
    assert [ln.problem for ln in lines[1:]] == ["gift card", "requires no shipping", "fully refunded or removed"]
    assert o.fulfillment_status == F.PENDING and qualifies(*facts(o), now=T0 + timedelta(hours=4))[0]


async def test_pending_to_review_and_a_resolved_review_waits_for_payment(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0)]
    await env.pull(T0)
    # FR-OB-15 + 16: an update before the 940 makes it invalid → Review (2026-10-29 edge)
    env.shop.orders = [shopify_order(1, 9301, financial="refunded", updated=T0 + timedelta(minutes=1), lines=[
        {"id": 11, "sku": "UNKNOWN", "variant_title": "Case", "quantity": 1, "price": "1", "requires_shipping": True}])]
    await env.pull(T0 + timedelta(minutes=1))
    o, _ = await env.order(1)
    assert o.fulfillment_status == F.REVIEW
    async with env.factory(SYSTEM_ACTOR) as u:  # the mapping is added…
        u.session.add(Item(item_code="UNKNOWN", sku="UNKNOWN", warehouse_held=True, version=1))
        await u.session.flush()
        u.session.add_all([ItemUnit(item_code="UNKNOWN", unit="EA", factor_ea=1, version=1),
                           ItemUnit(item_code="UNKNOWN", unit="CASE", factor_ea=6, version=1)])
        await u.commit()
    async with env.factory(OP) as u:  # …and a person re-checks
        r = await OrderService(u).resolve_review(o.id, env.tasks)
        await u.commit()
    assert r.remaining == [] and r.order.fulfillment_status == F.PAYMENT_HOLD  # refunded: not paid (FR-OB-12)
    assert (await env.q("SELECT is_completed FROM tasks"))[0]["is_completed"] is True


async def test_source_cancellation_before_the_940_cancels_silently(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0)]
    await env.pull(T0)
    env.shop.orders = [shopify_order(1, 9301, updated=T0 + timedelta(minutes=5), cancelled_at=T0.isoformat())]
    await env.pull(T0 + timedelta(minutes=5))
    o, _ = await env.order(1)
    assert o.fulfillment_status == F.CANCELLED and await env.q("SELECT 1 FROM tasks") == []


async def test_below_the_cutoff_is_historical_and_never_qualifies(env):
    env.shop.orders = [shopify_order(1, 8999, updated=T0)]
    await env.pull()
    o, _ = await env.order(1)
    assert o.historical and not qualifies(*facts(o), now=T0 + timedelta(days=9))[0]


# ── 850s: AC-OB-12 / 13 / 14 / 15, FR-OB-03 DC routing ──────────────────────

def po(partner, number, lines, ship_to_gln="0012345000099"):
    return {"TradingPartner": partner, "PurchaseOrderNumber": number, "PODate": "2026-10-28",
            "RequestedDeliveryDate": "2026-11-05", "ShipToCode": ship_to_gln, "ShipToName": "DC",
            "Lines": lines}


async def store_850(env, body: dict) -> int:
    data = json.dumps(body).encode()
    import hashlib
    async with env.factory(SYSTEM_ACTOR) as u:
        d = Document(direction="IN", origin="PARTNER", system="TRUECOMMERCE", kind="850_JSON", remote_name="po.json",
                     size=len(data), sha256=hashlib.sha256(data).hexdigest(), content=data, processing_status="Pending")
        u.session.add(d)
        await u.session.flush()
        await u.commit()
        return d.id


async def run_850(env, body) -> dict:
    doc_id = await store_850(env, body)
    out = await EdiIntake(uow_factory=env.factory, tasks=env.tasks).run_pass()
    [doc] = await env.q("SELECT processing_status, error FROM sales_documents WHERE id = :i", i=doc_id)
    return out | {"doc": dict(doc)}


async def test_ac12_ac14_retailer_lines_by_gtin_upc_and_this_partners_buyer_part(env):
    out = await run_850(env, {"PurchaseOrder": [
        po("LBL", "PO-1", [
            {"Line": 1, "GTIN#": "10062639300019", "Quantity": 10, "UOM": "CA", "ofInnerPacks": 6},  # AC-OB-14
            {"Line": 2, "UPC": "062639300029", "Quantity": 1, "UOM": "EA"},
            {"Line": 3, "BuyerPart": "L-555", "Quantity": 2, "UOM": "CA"}]),
        po("SOB", "PO-2", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}]),  # Loblaws' part
    ]})
    assert out["processed"] == 1 and out["orders"] == 2 and out["doc"]["processing_status"] == "Processed"
    o1, l1 = await env.order("PO-1")
    assert [(ln.item_code, ln.qty_ea) for ln in l1] == [("CF0092", 60), ("CF0010", 1), ("CF0092", 12)]
    assert o1.fulfillment_status == F.PENDING
    o2, l2 = await env.order("PO-2")
    assert o2.fulfillment_status == F.REVIEW and l2[0].item_code is None  # never another partner's part


async def test_an_850_line_without_a_unit_is_never_guessed(env):
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-9", [{"Line": 1, "GTIN#": "10062639300019", "Quantity": 2}])]})
    o, [line] = await env.order("PO-9")
    assert o.fulfillment_status == F.REVIEW and line.qty_ea is None
    assert line.problem == "the unit could not be recognised"


async def test_ac15_a_case_pack_other_than_the_item_master_goes_to_review(env):
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-3", [
        {"Line": 1, "GTIN#": "10062639300019", "Quantity": 10, "UOM": "CA", "ofInnerPacks": 12}])]})
    o, lines = await env.order("PO-3")
    assert o.fulfillment_status == F.REVIEW and "case pack 12 on the 850 differs" in lines[0].problem
    assert (await env.q("SELECT type FROM tasks"))[0]["type"] == "sales_order_review"


async def test_fr03_a_walmart_order_without_dc_routing_goes_to_review(env):
    await run_850(env, {"PurchaseOrder": [
        po("WMT", "W-1", [{"Line": 1, "BuyerPart": "W-999", "Quantity": 1, "UOM": "CA"}], ship_to_gln="0012345000099"),
        po("WMT", "W-2", [{"Line": 1, "BuyerPart": "W-999", "Quantity": 1, "UOM": "CA"}], ship_to_gln="0012345000011"),
    ]})
    w1, _ = await env.order("W-1")
    w2, _ = await env.order("W-2")
    assert w1.fulfillment_status == F.REVIEW and "no DC routing" in w1.review_reason
    assert w2.fulfillment_status == F.PENDING


async def test_unknown_partner_and_zero_quantity_go_to_review_and_a_bad_file_fails(env):
    await run_850(env, {"PurchaseOrder": [po("NEW", "N-1", [{"Line": 1, "GTIN#": "10062639300019", "Quantity": 0, "UOM": "CA"}])]})
    o, lines = await env.order("N-1")
    assert o.fulfillment_status == F.REVIEW and "unknown partner NEW" in o.review_reason
    assert lines[0].problem == "zero quantity"
    out = await run_850(env, {"Invoice": {}})
    assert out["failed"] == 1 and out["doc"]["processing_status"] == "Failed"
    assert "sales_order_file_bad" in {t["type"] for t in await env.q("SELECT type FROM tasks")}


async def test_an_850_resend_updates_the_order(env):
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-9", [{"Line": 1, "GTIN#": "10062639300019", "Quantity": 1, "UOM": "CA"}])]})
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-9", [{"Line": 1, "GTIN#": "10062639300019", "Quantity": 4, "UOM": "CA"}])],
                        "Resent": True})
    _, lines = await env.order("PO-9")
    assert len(await env.q("SELECT 1 FROM sales_orders")) == 1 and lines[0].qty_ea == 24


# ── AC-OB-19: manual orders ─────────────────────────────────────────────────

async def test_ac19_a_manual_draft_is_never_queued_and_after_submit_it_is(env):
    from app.schemas.orders import ManualLineIn, ManualOrderIn
    data = ManualOrderIn(partner_code="MAN", customer_reference="staff-1", lines=[
        ManualLineIn(item_code="CF0092", quantity=Decimal(2), unit="CASE")])
    async with env.factory(OP) as u:
        o = await ManualOrderService(u).create(data)
        await u.commit()
    assert o.fulfillment_status == F.DRAFT
    assert not qualifies(*facts(o, delay=0), now=T0 + timedelta(days=9))[0]
    async with env.factory(OP) as u:
        o = await ManualOrderService(u).submit(o.id, o.version)
        await u.commit()
    assert o.fulfillment_status == F.PENDING and o.paid_at is not None
    assert qualifies(*facts(o, delay=0), now=o.paid_at)[0]
    _, lines = await env.order(o.source_order_id)
    assert lines[0].qty_ea == 12


async def test_a_manual_order_with_a_bad_line_cannot_be_submitted(env):
    from app.platform.errors import InvalidInput
    from app.schemas.orders import ManualLineIn, ManualOrderIn
    async with env.factory(OP) as u:
        preview = await ManualOrderService(u).preview([ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="PALLET")])
        o = await ManualOrderService(u).create(ManualOrderIn(partner_code="MAN", lines=[
            ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="PALLET")]))
        await u.commit()
    assert preview[0].problem == "no conversion for unit PALLET"
    async with env.factory(OP) as u:
        with pytest.raises(InvalidInput, match="fix every line"):
            await ManualOrderService(u).submit(o.id, o.version)
    async with env.factory(OP) as u:
        with pytest.raises(InvalidInput, match="MANUAL-channel"):
            await ManualOrderService(u).create(ManualOrderIn(partner_code="SHOP", lines=[
                ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="EA")]))


async def test_ac79_a_fraction_of_an_ea_goes_to_review_never_rounded(env):
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-F", [
        {"Line": 1, "GTIN#": "10062639300019", "Quantity": "0.25", "UOM": "CA"}])]})
    o, lines = await env.order("PO-F")
    assert o.fulfillment_status == F.REVIEW and lines[0].qty_ea is None
    assert lines[0].problem.startswith("0.25 CASE is 1.5") and lines[0].problem.endswith("EA, not a whole number")


async def test_ac23_a_paid_order_refunded_and_archived_is_suppressed_without_a_task(env):
    env.shop.orders = [shopify_order(1, 9301, updated=T0)]
    await env.pull(T0)
    later = T0 + timedelta(minutes=20)
    env.shop.orders = [shopify_order(1, 9301, financial="refunded", updated=later, closed_at=later.isoformat())]
    await env.pull(later)
    o, _ = await env.order(1)
    assert (o.fulfillment_status, o.archive_suppressed) == (F.ARCHIVE_SUPPRESSED, True)
    assert await env.q("SELECT 1 FROM tasks") == []  # refunded or voided orders raise no task (FR-OB-03d)
