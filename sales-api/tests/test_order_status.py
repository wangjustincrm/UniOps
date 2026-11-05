"""What people see of an order (PRD 9.1): every internal status combination gives
its display values; the SQL the lists use is the same rules; no list has a
condition of its own."""
import ast
import itertools
import pathlib
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.domain.order_status import (
    ARCHIVED, CANCELLED_AT_SOURCE, COMPLETED, COMPLETED_SHADOW, CONFLICT_945, DELIVERY_SHADOW, HOLD_REASONS, ON_HOLD, ORDER_STATUSES, SHOPIFY_FAILED, TIMEOUT_997, WAITING_945, WITH_ZOHO, DetailFacts, StatusFacts,
    display, status_detail,
)
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.documents import Document
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.services.health import DASHBOARD_CARDS
from app.services.order_status_sql import ON_HOLD_REASON_SQL, ORDER_STATUS_SQL
from app.services.order_views import OrderRepository, OrderService, status_facts
from app.services.orders import OrderSync
from tests.test_orders import OP, T0, env, shopify_order  # noqa: F401 — env is a fixture

ALL_F = [s.value for s in F]
ALL_I = [s.value for s in Inv]

# fulfillment → (Order status, reason) when the invoice has nothing to say
STATUS_OF = {"Draft": ("Draft", None), "Review": (ON_HOLD, "Needs Review"), "Payment Hold": (ON_HOLD, "Payment Required"),
             "Pending": ("Waiting to Send", None), "Archive Suppressed": (ON_HOLD, ARCHIVED), "940 Generated": ("Waiting to Send", None),
             "940 Sent": ("Waiting for 997", None), "940 Accepted": ("Waiting for Packing List", None),
             "940 Rejected": (ON_HOLD, "940 Rejected"), "940 Timeout": (ON_HOLD, TIMEOUT_997), "940 Failed": (ON_HOLD, "940 Failed"),
             "On Hold": (ON_HOLD, CONFLICT_945), "Cancelled": ("Cancelled", None)}
SHIPPED_BY_INVOICE = {"Not Required": COMPLETED, "Pending": "Waiting for Delivery", "Awaiting Requested Date": "Waiting for Delivery",
                      "Waiting for Delivery Confirmation": "Waiting for Delivery", "Ready": "Waiting for Invoice",
                      "Queued": "Waiting for Invoice", "Sent": COMPLETED}


@pytest.mark.parametrize(("fulfillment", "invoice"), list(itertools.product(ALL_F, ALL_I)))
def test_every_internal_combination_has_its_display(fulfillment, invoice):
    d = display(StatusFacts(fulfillment=fulfillment, invoice=invoice))
    if fulfillment in ("Draft", "Cancelled"):
        want = STATUS_OF[fulfillment]
    elif fulfillment in STATUS_OF and STATUS_OF[fulfillment][1]:
        want = STATUS_OF[fulfillment]  # the fulfillment's hold comes before the invoice's
    elif invoice == "Failed":
        want = (ON_HOLD, "Invoice Failed")
    elif invoice == "Blocked":
        want = (ON_HOLD, "Invoice Blocked")
    elif fulfillment == "Shipped":
        want = (SHIPPED_BY_INVOICE[invoice], None)
    else:
        want = STATUS_OF[fulfillment]
    assert (d.order_status, d.on_hold_reason) == want
    assert d.order_status in ORDER_STATUSES and d.short_shipped is False
    assert d.on_hold_reason is None or d.on_hold_reason in HOLD_REASONS


def show(**k):
    d = display(StatusFacts(**k))
    return d.label, d.short_shipped


CASES = [
    # a short shipment is final: closed by its one 945, and it goes on through delivery and invoice
    (dict(fulfillment="Shipped", invoice="Not Required", short=True, source_type="SHOPIFY"), (COMPLETED, True)),
    (dict(fulfillment="Shipped", invoice="Waiting for Delivery Confirmation", short=True, source_type="EDI_850", invoicing=True),
     ("Waiting for Delivery", True)),
    (dict(fulfillment="Shipped", invoice="Sent", short=True, invoicing=True), (COMPLETED, True)),
    # Shopify's write
    (dict(fulfillment="Shipped", invoice="Not Required", source_type="SHOPIFY", shopify_pending=True), (WAITING_945, False)),
    (dict(fulfillment="Shipped", invoice="Not Required", source_type="SHOPIFY", shopify_failed=True), (f"{ON_HOLD}: {SHOPIFY_FAILED}", False)),
    # the two ways into On Hold, and what it held before
    (dict(fulfillment="On Hold", cancelled_at_source=True), (f"{ON_HOLD}: {CANCELLED_AT_SOURCE}", False)),
    (dict(fulfillment="On Hold", short=True), (f"{ON_HOLD}: {CONFLICT_945}", True)),
    (dict(fulfillment="On Hold", cancelled_at_source=True), (f"{ON_HOLD}: {CANCELLED_AT_SOURCE}", False)),
    # archived before the re-check fix: left Pending
    (dict(fulfillment="Pending", archived=True, source_type="SHOPIFY", invoice="Not Required"), (f"{ON_HOLD}: {ARCHIVED}", False)),
    # the shadow run
    (dict(fulfillment="Pending", shadow=True, invoice="Pending"), (WITH_ZOHO, False)),
    (dict(fulfillment="Pending", shadow=True, source_type="SHOPIFY", has_945=True, invoice="Not Required"), (COMPLETED_SHADOW, False)),
    # Zoho fulfills and archives before the goods leave: only a 945 proves shipped, and no hold either
    (dict(fulfillment="Pending", shadow=True, source_type="SHOPIFY", archived=True, fulfilled_in_shopify=True, invoice="Not Required"),
     (WITH_ZOHO, False)),
    (dict(fulfillment="Archive Suppressed", shadow=True, source_type="SHOPIFY", archived=True, fulfilled_in_shopify=True, invoice="Not Required"),
     (WITH_ZOHO, False)),
    (dict(fulfillment="Pending", shadow=True, source_type="SHOPIFY", fulfilled_in_shopify=True, invoice="Not Required"), (WITH_ZOHO, False)),
    (dict(fulfillment="Archive Suppressed", shadow=True, source_type="SHOPIFY", archived=True, fulfilled_in_shopify=True, has_945=True,
          invoice="Not Required"), (COMPLETED_SHADOW, False)),
    # outside the shadow run nothing changes: an Archive Suppressed order is a hold
    (dict(fulfillment="Archive Suppressed", source_type="SHOPIFY", archived=True, fulfilled_in_shopify=True, invoice="Not Required"),
     (f"{ON_HOLD}: {ARCHIVED}", False)),
    (dict(fulfillment="Archive Suppressed", shadow=True, source_type="SHOPIFY", archived=True, invoice="Not Required"), (f"{ON_HOLD}: {ARCHIVED}", False)),
    (dict(fulfillment="Pending", shadow=True, source_type="SHOPIFY", archived=True, invoice="Not Required"), (f"{ON_HOLD}: {ARCHIVED}", False)),
    (dict(fulfillment="940 Timeout", shadow=True, source_type="SHOPIFY", has_945=True, fulfilled_in_shopify=True, invoice="Not Required"),
     (f"{ON_HOLD}: {TIMEOUT_997}", False)),  # order 23
    (dict(fulfillment="Pending", shadow=True, source_type="FROM_945", has_945=True, invoicing=True, invoice="Not Required"), (DELIVERY_SHADOW, False)),
    (dict(fulfillment="Pending", shadow=True, source_type="FROM_945", has_945=True, invoice="Not Required"), (COMPLETED_SHADOW, False)),  # order 107
    (dict(fulfillment="Pending", shadow=True, source_type="EDI_850", has_945=True, invoicing=True), (DELIVERY_SHADOW, False)),
    (dict(fulfillment="Review", shadow=True), (f"{ON_HOLD}: Needs Review", False)),
    (dict(fulfillment="Cancelled", shadow=True, has_945=True), ("Cancelled", False)),
    (dict(fulfillment="Draft", shadow=True), ("Draft", False)),
]


@pytest.mark.parametrize(("facts", "want"), CASES)
def test_short_shipment_on_hold_and_shadow_cases(facts, want):
    assert show(**facts) == want


def message(n=None, **k):
    f = StatusFacts(**k)
    return status_detail(display(f), f, n or DetailFacts())


@pytest.mark.parametrize(("facts", "n", "want"), [
    (dict(fulfillment="Review"), DetailFacts(review_reason="line 1: item not in the Item Master\nsecond"), ("line 1: item not in the Item Master", None)),
    (dict(fulfillment="Pending"), DetailFacts(send_at=T0), ("Sends at", T0)),
    (dict(fulfillment="Pending"), DetailFacts(manual_sending=True), ("This partner sends by hand", None)),
    (dict(fulfillment="940 Sent"), DetailFacts(sent_at=T0), ("Since", T0)),
    (dict(fulfillment="940 Accepted"), DetailFacts(sent_at=T0), ("Since", T0)),
    (dict(fulfillment="940 Timeout"), DetailFacts(sent_at=T0), ("No 997 since", T0)),
    (dict(fulfillment="940 Rejected"), DetailFacts(file_note="AK9 R"), ("AK9 R", None)),
    (dict(fulfillment="Shipped", invoice="Not Required", shopify_failed=True), DetailFacts(shopify_error="the fulfillment: 403"), ("the fulfillment: 403", None)),
    (dict(fulfillment="Shipped", invoice="Failed"), DetailFacts(invoice_error="no connection"), ("no connection", None)),
    (dict(fulfillment="Shipped", invoice="Not Required", shopify_pending=True), DetailFacts(), ("Writing the tracking to Shopify", None)),
    (dict(fulfillment="Archive Suppressed", archived=True), DetailFacts(archived_at=T0), ("Archived", T0)),
    (dict(fulfillment="On Hold", cancelled_at_source=True), DetailFacts(cancelled_at=T0), ("Cancelled", T0)),
])
def test_the_status_detail_adds_what_the_status_does_not_say(facts, n, want):
    assert message(n, **facts) == want


@pytest.mark.parametrize("facts", [
    dict(fulfillment="Shipped", invoice="Waiting for Delivery Confirmation"),  # no "Confirm delivery"
    dict(fulfillment="Shipped", invoice="Ready"), dict(fulfillment="Shipped", invoice="Sent"),
    dict(fulfillment="Shipped", invoice="Not Required"), dict(fulfillment="Draft"), dict(fulfillment="Cancelled"),
    dict(fulfillment="Payment Hold"), dict(fulfillment="On Hold"), dict(fulfillment="940 Generated"),
    dict(fulfillment="Pending", shadow=True),
    dict(fulfillment="Pending", shadow=True, has_945=True, source_type="SHOPIFY"),
])
def test_the_status_detail_is_empty_when_it_has_nothing_to_add(facts):
    full = DetailFacts(send_at=T0, sent_at=T0, requested_date=T0.date(), file_note="x", shopify_error="x", invoice_error="x")
    assert message(full, **facts) == (None, None)


def test_every_combination_has_a_message_or_none_without_failing():
    for f, i in itertools.product(ALL_F, ALL_I):
        text, _ = message(fulfillment=f, invoice=i)
        assert text is None or isinstance(text, str)


async def test_the_sql_gives_what_the_function_gives_for_every_combination(env):  # noqa: F811
    combos = list(itertools.product(ALL_F, ALL_I, (False, True), (False, True), ("SHOP", "LBL", "NOPE")))
    async with env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        doc = Document(direction="IN", origin="PARTNER", system="RADIANT", kind="945_EDI", remote_name="x", size=1, sha256="a" * 64,
                       content=b"x", processing_status="Skipped")
        s.add(doc)
        await s.flush()
        i = 0
        for fu, inv, shadow, extra, partner in combos:
            i += 1
            o = Order(partner_code=partner, source_type="SHOPIFY" if partner == "SHOP" else "EDI_850", source_order_id=f"s{i}",
                      fulfillment_status=fu, invoice_status=inv, shadow=shadow,
                      first_received_at=T0, archived_in_shopify_at=T0 if extra else None,
                      shopify_fulfillment_status="fulfilled" if i % 3 == 0 else None, source_cancelled_at=T0 if i % 5 == 0 else None,
                      version=1)
            s.add(o)
            await s.flush()
            if i % 2:
                s.add(Shipment(document_id=doc.id, set_index=i, order_id=o.id, applied=False, shadow=shadow, version=1,
                               shopify_status=("Not needed", "Failed", "Pending", "Done", "Suppressed")[i % 5]))
            if i % 4 == 0:
                s.add(OrderLine(order_id=o.id, line_no=1, source={}, ordered_qty=5, qty_ea=5, shipped_ea=3))
        await u.commit()
    async with env.factory(SYSTEM_ACTOR) as u:
        rows = (await u.session.execute(sa.select(Order, ORDER_STATUS_SQL, ON_HOLD_REASON_SQL))).all()
    assert len(rows) == i
    for o, status, reason in rows:
        d = display(status_facts(o))
        assert (d.order_status, d.on_hold_reason) == (status, reason if status == ON_HOLD else None), o.source_order_id


def test_no_list_or_count_has_a_condition_of_its_own():
    """Home and Health count through the derived fields; only eligibility reads the raw lines."""
    for _, _, link in DASHBOARD_CARDS:
        assert not {f for f, _, _ in link.filters} & {"fulfillment_status", "invoice_status", "order_state"}
    root = pathlib.Path("app/services")
    for name in ("health.py", "inbox.py"):
        tree = ast.parse((root / name).read_text())
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not strings & {"fulfillment_status", "invoice_status", "order_state"}, name
    # the front end shows the derived values: raw status only on Overview, the actions and diagnostics
    pages = pathlib.Path("../sales/src/features/orders/OrdersPage.tsx").read_text()
    assert "o.order_state" not in pages


def test_views_keep_the_old_fields_and_the_presets_use_fields_the_list_has():
    repo = OrderRepository
    offered = {f["name"] for f in repo.field_meta()}
    for old in ("fulfillment_status", "invoice_status", "order_state", "hold_reason"):
        assert old in repo.filterable and old not in offered  # a saved view still lists; no list offers it
    assert {"order_status", "on_hold_reason", "shopify_financial_status", "shopify_fulfillment_status",
            "archived_in_shopify_at", "source_cancelled_at"} <= offered
    for p in repo.presets:
        for f in p.state.get("filters", []):
            assert f["field"] in repo.filterable, p.key


async def test_the_short_shipped_mark_counts_the_shadow_945_only_when_its_rows_are_linked(env):  # noqa: F811
    async with env.factory(SYSTEM_ACTOR) as u:
        s = u.session
        doc = Document(direction="IN", origin="PARTNER", system="RADIANT", kind="945_EDI", remote_name="x", size=1, sha256="b" * 64,
                       content=b"x", processing_status="Skipped")
        s.add(doc)
        await s.flush()
        ids = {}
        for n, (linked, shipped) in enumerate(((True, 3), (True, 5), (False, 3)), start=1):
            o = Order(partner_code="SHOP", source_type="SHOPIFY", source_order_id=f"m{n}", fulfillment_status="Pending",
                      invoice_status="Not Required", shadow=True, first_received_at=T0, version=1)
            s.add(o)
            await s.flush()
            ln = OrderLine(order_id=o.id, line_no=1, item_code="CF0092", source={}, ordered_qty=5, qty_ea=5)
            sh = Shipment(document_id=doc.id, set_index=n, order_id=o.id, applied=False, shadow=True, version=1, shopify_status="Not needed")
            s.add_all([ln, sh])
            await s.flush()
            s.add(ShipmentRow(shipment_id=sh.id, lx_index=1, w12_index=1, qty_shipped_ea=shipped, order_line_id=ln.id if linked else None))
            ids[n] = o.id
        await u.commit()
    async with env.factory(SYSTEM_ACTOR) as u:
        got = {n: display(status_facts((await u.session.execute(sa.select(Order).where(Order.id == i))).scalar_one())).short_shipped
               for n, i in ids.items()}
    assert got == {1: True, 2: False, 3: False}  # short; shipped in full; an unlinked 945 says nothing


async def test_a_stored_view_on_the_old_values_still_lists(env):  # noqa: F811
    await env.pull()
    from app.platform.query import Filter, QuerySpec
    async with env.factory(OP) as u:
        for spec in (Filter(field="fulfillment_status", op="eq", value="Review"), Filter(field="order_state", op="eq", value="Active"),
                     Filter(field="invoice_status", op="eq", value="Not Required")):
            assert (await OrderService(u).list(QuerySpec(page=1, page_size=5, filters=[spec]))).total >= 0


async def test_recheck_applies_the_archive_rule(env):  # noqa: F811
    bad = [{"id": 5, "sku": "NOPE", "variant_title": "Case", "quantity": 1, "price": "1", "requires_shipping": True}]
    env.shop.orders = [shopify_order(1, 9301, lines=bad)]
    await env.pull()
    async with env.factory(SYSTEM_ACTOR) as u:
        o = (await u.session.execute(sa.select(Order))).scalar_one()
        assert o.fulfillment_status == F.REVIEW
        o.archived_in_shopify_at = T0 - timedelta(hours=1)  # archived while it waited in review
        (await u.session.execute(sa.select(OrderLine))).scalars().first().source = {"sku": "CF0092-SKU", "variant_title": "Case"}
        await u.commit()
    async with env.factory(OP) as u:
        o = (await u.session.execute(sa.select(Order).with_for_update())).scalar_one()
        assert await OrderSync(u, env.tasks).recheck(o) == []
        await u.commit()
    o, _ = await env.order(1)
    assert o.fulfillment_status == F.ARCHIVE_SUPPRESSED and o.archive_suppressed is True
    assert o.archived_in_shopify_at is not None
    assert await env.q("SELECT 1 FROM tasks WHERE type = 'sales_order_archived'")


async def test_recheck_of_an_order_not_archived_goes_to_pending(env):  # noqa: F811
    bad = [{"id": 5, "sku": "NOPE", "variant_title": "Case", "quantity": 1, "price": "1", "requires_shipping": True}]
    env.shop.orders = [shopify_order(1, 9301, lines=bad)]
    await env.pull()
    async with env.factory(SYSTEM_ACTOR) as u:
        (await u.session.execute(sa.select(OrderLine))).scalars().first().source = {"sku": "CF0092-SKU", "variant_title": "Case"}
        await u.commit()
    async with env.factory(OP) as u:
        o = (await u.session.execute(sa.select(Order).with_for_update())).scalar_one()
        await OrderSync(u, env.tasks).recheck(o)
        await u.commit()
    o, _ = await env.order(1)
    assert o.fulfillment_status == F.PENDING


