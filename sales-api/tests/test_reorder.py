"""Reorder for this customer (user 2026-11-19): a gift, a replacement or a sample goes through Sales as a new order,
not as a 945 the warehouse sends on its own. The reorder is a Draft manual order copied from the source (ship-to,
contact, carrier, its Order ref, a note, a link back) with no lines; a reason is required to submit; its 940 goes
like any manual order's; the source's Activity names it; Orders → Manual orders by reason lists it."""
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.status import Fulfillment as F
from app.domain.x12 import segments
from app.platform.errors import BusinessError
from app.platform.query import QuerySpec, parse_filter
from app.schemas.orders import AddressIn, ManualLineIn, ManualOrderIn
from app.services.order_views import ManualOrderService, OrderRepository, OrderService
from tests import acting
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_outbound import edi  # noqa: F401 — a fixture


async def test_reorder_copies_the_customer_as_a_draft_with_no_lines_and_a_reason_is_required_to_submit(edi):  # noqa: F811
    env_ = edi.env
    env_.shop.orders = [shopify_order(1, 9439)]
    await env_.pull()
    [source] = [r for r in await env_.q("SELECT id, source_reference, ship_to FROM sales_orders")]

    async with env_.factory(OP) as u:
        draft = await ManualOrderService(u).reorder(source["id"])
        await u.commit()
    async with env_.factory(OP) as u:
        o = await OrderService(u).get(draft.id)
    assert o.source_type == "MANUAL"
    assert draft.fulfillment_status == F.DRAFT and o.partner_code == "MAN"
    assert (o.source_order_number, o.parent_order_id, o.parent_reference) == (source["source_reference"], source["id"],
                                                                            source["source_reference"])
    from app.services.order_views import manual_address
    assert o.ship_to == manual_address(source["ship_to"])  # in a manual order's shape (no company, codes)
    lines = await env_.q(f"SELECT id FROM sales_order_lines WHERE order_id = {draft.id}")
    assert lines == []  # the lines are the person's to add

    # Submit needs a reason, then a line
    # the person completes what the source lacks (this one has no phone) while editing the draft
    ship = AddressIn(**({"phone": "514-555-0100"} | {k: v for k, v in source["ship_to"].items()
                                                     if k in AddressIn.model_fields and v is not None}))
    async with env_.factory(OP) as u:
        o2 = await ManualOrderService(u).update(draft.id, ManualOrderIn(
            partner_code="MAN", customer_reference=source["source_reference"], ship_to=ship, carrier="Purolator",
            lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]), draft.version)
        await u.commit()
    async with env_.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await acting.submit(u, o2.id, o2.version)
    assert e.value.code == "order.reason"
    async with env_.factory(OP) as u:
        o2 = await ManualOrderService(u).update(draft.id, ManualOrderIn(
            reason="Gift", partner_code="MAN", customer_reference=source["source_reference"], ship_to=ship, carrier="Purolator",
            lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]), o2.version)
        o2 = await acting.submit(u, o2.id, o2.version)
        await u.commit()
    assert o2.fulfillment_status == F.PENDING and o2.manual_reason == "Gift"

    # Its 940 goes like any manual order's, under its own number (its 945 will match it, not the source)
    edi.clock.now = o2.paid_at + timedelta(minutes=1)
    r = await edi.out.queue_due()
    assert r.invalid == 0  # the source (a paid Shopify order) goes too, under its own number
    while (await edi.worker.run_once()).done:
        pass
    w05 = [[s for s in segments(c) if s[0] == "W05"][0][2] for c in edi.store.files.values()]
    assert f"{o2.order_number:09d}" in w05

    # The source's Activity names it; Manual orders by reason lists it
    async with env_.factory(OP) as u:
        detail = await OrderService(u).detail(source["id"])
        meta = await OrderService(u).meta()
        view = next(v for v in meta["views"] if v["key"] == "manual_by_reason")
        free = await OrderRepository(u).list(QuerySpec(filters=[parse_filter(f"{f['field']}:{f['op']}:" + (
            "|".join(f["value"]) if isinstance(f["value"], list) else str(f["value"]))) for f in view["state"]["filters"]], page_size=50))
    [entry] = [t for t in detail.timeline if t.action == "reorder"]
    assert entry.reason == f"Reorder {o2.source_reference} (Gift)" and entry.after["order_id"] == o2.id
    [row] = free.items
    assert (row.id, row.manual_reason, row.ordered_ea, row.parent_reference) == (o2.id, "Gift", 6, source["source_reference"])
    assert view["state"]["group"] == [{"field": "received_month", "desc": True}, {"field": "manual_reason", "desc": False}]


async def test_a_reorder_of_a_manual_order_keeps_its_partner_and_a_draft_with_no_lines_cannot_be_submitted(edi):  # noqa: F811
    env_ = edi.env
    ship = AddressIn(name="Staff shop", phone="613-555-0100", address1="1680 Venture Dr", city="Kingston", province="ON", zip="K7P 0E9")
    async with env_.factory(OP) as u:
        first = await ManualOrderService(u).create(ManualOrderIn(reason="Phone order", partner_code="MAN", ship_to=ship, carrier="LTL",
                                                                 lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]))
        await u.commit()
    async with env_.factory(OP) as u:
        again = await ManualOrderService(u).reorder(first.id)
        await u.commit()
    assert (again.partner_code, again.shipping_method, again.notes) == ("MAN", "LTL", f"Reorder of {first.source_reference}")
    async with env_.factory(OP) as u:
        again = await u.session.get(type(again), again.id)
        again.manual_reason = "Replacement"
        await u.commit()
        version = again.version
    async with env_.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await acting.submit(u, again.id, version)
    assert e.value.code == "order.no_lines"


async def test_the_reasons_are_a_setting_and_submit_takes_only_one_of_them(edi):  # noqa: F811
    """User 2026-11-19: the reason list is configurable (Orders → Manual order settings); no separate "free" list."""
    from app.services.order_settings import ManualOrderSettingsService
    env_ = edi.env
    ship = AddressIn(name="Shop", phone="613-555-0100", address1="1 Rd", city="Kingston", province="ON", zip="K7P 0E9")
    async with env_.factory(OP) as u:
        s = await ManualOrderSettingsService(u).get()
        assert s.reasons == ["Gift", "Replacement", "Sample", "Staff purchase", "Phone order", "Other"]
        await ManualOrderSettingsService(u).update(s.model_copy(update={"reasons": ["Promotion", "Gift"]}))
        await u.commit()
    async with env_.factory(OP) as u:
        o = await ManualOrderService(u).create(ManualOrderIn(reason="Sample", partner_code="MAN", ship_to=ship, carrier="LTL",
                                                             lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]))
        await u.commit()
    async with env_.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await acting.submit(u, o.id, o.version)
    assert e.value.code == "order.reason" and "Promotion, Gift" in str(e.value)



async def test_a_reorder_of_a_shopify_order_saves_and_submits_with_the_address_in_a_manual_order_s_shape(edi):  # noqa: F811
    """Review 2026-11-19: Shopify's ship-to has company, country_code, province_code (and may name the country and
    province in full); the reorder keeps AddressIn's shape, so the draft's own address saves and submits as it is."""
    from app.models.orders import Order
    from app.services.order_views import manual_address
    env_ = edi.env
    env_.shop.orders = [shopify_order(1, 9501)]
    await env_.pull()
    [src] = await env_.q("SELECT id FROM sales_orders")
    async with env_.factory(OP) as u:
        o = await u.session.get(Order, src["id"])
        o.ship_to = {"name": "Jane Doe", "company": "Acme", "phone": "416-555-0100", "address1": "1 Main St", "address2": None,
                     "city": "Toronto", "province": "Ontario", "province_code": "ON", "country": "Canada", "country_code": "CA",
                     "zip": "M5H 1A1"}
        await u.commit()
    async with env_.factory(OP) as u:
        draft = await ManualOrderService(u).reorder(src["id"])
        await u.commit()
    assert draft.ship_to == {"name": "Jane Doe", "attention": "Acme", "phone": "416-555-0100", "address1": "1 Main St",
                             "city": "Toronto", "zip": "M5H 1A1", "country": "CA", "province": "ON"}
    assert manual_address(None) == {}
    async with env_.factory(OP) as u:
        d = await ManualOrderService(u).update(draft.id, ManualOrderIn(
            reason="Gift", partner_code="MAN", ship_to=AddressIn(**draft.ship_to), carrier="Purolator",
            lines=[ManualLineIn(item_code="CF0092", quantity=Decimal(1), unit="CASE")]), draft.version)
        d = await acting.submit(u, d.id, d.version)
        await u.commit()
    assert d.fulfillment_status == F.PENDING
