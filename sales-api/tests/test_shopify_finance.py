"""Shopify finance (user 2026-11-03): with every order the pass reads its
transactions and stores its payments, refunds (lines, adjustments) and the
summary — channel, tags, gateways, discount codes, tax lines, original total,
refunded, net; a later refund updates them; orders already here are filled a
few per pass; no card data anywhere; FR-OB-11b untouched."""
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.domain.shopify_finance import finance_of
from app.platform.actors import SYSTEM_ACTOR
from app.platform.query import QuerySpec, parse_filter
from app.services.order_views import OrderRepository, OrderService
from app.integrations.shopify import ShopifyError
from tests.test_orders import OP, T0, env, shopify_order  # noqa: F401 — env is a fixture

CARD = {"credit_card_bin": "424242", "credit_card_number": "•••• •••• •••• 4242", "credit_card_name": "A B",
        "credit_card_expiration_month": 1, "credit_card_expiration_year": 2030, "avs_result_code": "Y", "cvv_result_code": "M"}
SALE = {"id": 7001, "kind": "sale", "gateway": "shopify_payments", "status": "success", "amount": "100.00", "currency": "CAD",
        "processed_at": "2026-10-29T08:01:00-04:00", "authorization": "ch_1", "payment_id": "c1.1", "test": False,
        "payment_details": CARD, "receipt": {"payment_id": "x"}}
REFUND_TX = {"id": 7002, "kind": "refund", "gateway": "shopify_payments", "status": "success", "amount": "37.00",
             "currency": "CAD", "parent_id": 7001, "processed_at": "2026-10-30T09:00:00-04:00", "payment_details": CARD}
REFUND = {"id": 901, "created_at": "2026-10-30T09:00:00-04:00", "processed_at": "2026-10-30T09:00:00-04:00",
          "note": "damaged in transit", "restock": False, "transactions": [REFUND_TX],
          "refund_line_items": [{"id": 951, "line_item_id": 11, "quantity": 1, "subtotal": "25.00", "total_tax": "3.25",
                                 "restock_type": "no_restock", "line_item": {"sku": "CF0092-SKU", "title": "Formula"}}],
          "order_adjustments": [{"id": 961, "kind": "shipping_refund", "reason": "Shipping refund", "amount": "-7.75", "tax_amount": "-1.00"}]}


def financed(oid=1, number=9301, *, refunds=None, updated=T0, financial="paid"):
    o = shopify_order(oid, number, updated=updated, financial=financial, refunds=refunds, lines=[
        {"id": 11, "sku": "CF0092-SKU", "title": "Formula", "variant_title": "Case", "quantity": 2, "price": "50.00",
         "requires_shipping": True, "tax_lines": [{"title": "HST", "rate": 0.13, "price": "13.00"}],
         "discount_allocations": [{"amount": "5.00"}]}])
    return o | {"source_name": "web", "tags": "wholesale, vip", "payment_gateway_names": ["shopify_payments"],
                "discount_codes": [{"code": "WELCOME5", "amount": "5.00", "type": "fixed_amount"}],
                "tax_lines": [{"title": "HST", "rate": 0.13, "price": "13.00"}], "total_price": "113.00"}


def test_the_finance_of_an_order_from_its_json_and_transactions():
    f = finance_of(financed(refunds=[REFUND]), [SALE, REFUND_TX])
    assert (f.source_name, f.tags, f.payment_gateways) == ("web", "wholesale, vip", ["shopify_payments"])
    assert f.discount_codes == [{"code": "WELCOME5", "type": "fixed_amount", "amount": "5.00"}]
    assert f.tax_lines == [{"title": "HST", "rate": 0.13, "amount": "13.00"}]
    assert (f.original_total, f.total_refunded, f.net_amount) == (Decimal("113.00"), Decimal("37.00"), Decimal("76.00"))
    assert (f.lines[11].tax_amount, f.lines[11].discount_amount) == (Decimal("13.00"), Decimal("5.00"))
    [r] = f.refunds
    assert (r.lines_amount, r.shipping_amount, r.adjustments_amount, r.amount) == (
        Decimal("28.25"), Decimal("8.75"), Decimal("0.00"), Decimal("37.00"))
    assert [(p.shopify_id, p.kind, p.refund_shopify_id) for p in f.payments] == [(7001, "sale", None), (7002, "refund", 901)]
    # a failed refund is not refunded
    assert finance_of(financed(), [SALE, REFUND_TX | {"status": "failure"}]).total_refunded == Decimal("0.00")


async def stored(t):
    p = await t.q("SELECT shopify_id, kind, status, amount::text a, refund_id IS NOT NULL r, raw::text raw FROM sales_order_payments ORDER BY shopify_id")
    r = await t.q("SELECT shopify_id, note, lines_amount::text l, shipping_amount::text s, amount::text a, raw::text raw FROM sales_order_refunds")
    return p, r


async def test_the_pass_stores_payments_refunds_and_the_summary_without_card_data(env):  # noqa: F811
    env.shop.orders = [financed(refunds=[REFUND], financial="partially_refunded")]
    env.shop.transactions_by_order["1"] = [SALE, REFUND_TX]
    r = await env.pull()
    assert (r.created, r.status) == (1, "ok")
    o, lines = await env.order(1)
    assert (o.source_name, o.tags, o.payment_gateways, o.discount_codes[0]["code"]) == ("web", "wholesale, vip", ["shopify_payments"], "WELCOME5")
    assert (o.original_total, o.total_refunded, o.net_amount, o.finance_updated_at) == (
        Decimal("113.00"), Decimal("37.00"), Decimal("76.00"), o.source_updated_at)
    assert (lines[0].tax_amount, lines[0].discount_amount, lines[0].tax_lines[0]["title"]) == (Decimal("13.00"), Decimal("5.00"), "HST")
    pays, refunds = await stored(env)
    assert [(x["shopify_id"], x["kind"], x["a"], x["r"]) for x in pays] == [(7001, "sale", "100.00", False), (7002, "refund", "37.00", True)]
    assert [(x["note"], x["l"], x["s"], x["a"]) for x in refunds] == [("damaged in transit", "28.25", "8.75", "37.00")]
    [rl] = await env.q("SELECT quantity, subtotal::text s, order_line_id FROM sales_order_refund_lines")
    assert (rl["quantity"], rl["s"], rl["order_line_id"]) == (1, "25.00", lines[0].id)
    [adj] = await env.q("SELECT kind, amount::text a FROM sales_order_refund_adjustments")
    assert (adj["kind"], adj["a"]) == ("shipping_refund", "-7.75")
    # FR-OB-11b is the order sync's, unchanged: 2 ordered, 1 refunded → 1 to ship
    assert lines[0].ordered_qty == Decimal("1")
    # no card data anywhere: the rows, their raw JSON, the stored order page
    docs = await env.q("SELECT convert_from(content, 'UTF8') c FROM sales_documents WHERE kind = 'SHOPIFY_ORDER_JSON'")
    for text in [x["raw"] for x in pays + refunds] + [d["c"] for d in docs]:
        for banned in ("424242", "4242", "credit_card", "payment_details", "avs_result_code", "receipt"):
            assert banned not in text, banned


async def test_a_later_refund_updates_the_rows_and_the_net_without_duplicates(env):  # noqa: F811
    env.shop.orders = [financed()]
    env.shop.transactions_by_order["1"] = [SALE]
    await env.pull()
    o, _ = await env.order(1)
    assert (o.total_refunded, o.net_amount) == (Decimal("0.00"), Decimal("113.00"))
    later = T0 + timedelta(hours=2)
    env.shop.orders = [financed(refunds=[REFUND], updated=later, financial="partially_refunded")]
    env.shop.transactions_by_order["1"] = [SALE, REFUND_TX]
    r = await env.pull()
    assert r.updated == 1
    o, _ = await env.order(1)
    assert (o.total_refunded, o.net_amount, o.shopify_financial_status) == (Decimal("37.00"), Decimal("76.00"), "partially_refunded")
    env.shop.orders = [financed(refunds=[REFUND], updated=later + timedelta(minutes=1), financial="partially_refunded")]
    await env.pull()  # the same refund seen again
    assert [x["n"] for x in await env.q("SELECT count(*) n FROM sales_order_payments UNION ALL SELECT count(*) FROM sales_order_refunds")] == [2, 1]


async def test_orders_already_here_are_filled_a_few_per_pass_reading_only(env):  # noqa: F811
    env.shop.orders = [financed()]
    env.shop.transactions_by_order["1"] = [SALE]
    await env.pull()
    async with env.factory(SYSTEM_ACTOR) as u:  # as if imported before sales17
        await u.session.execute(sa.text("UPDATE sales_orders SET finance_updated_at = NULL, net_amount = NULL"))
        await u.session.execute(sa.text("DELETE FROM sales_order_payments"))
        await u.commit()
    env.shop.calls.clear()
    env.shop.orders = [financed()]  # nothing newer than the cursor but the boundary order itself
    r = await env.pull()
    o, _ = await env.order(1)
    assert o.net_amount == Decimal("113.00") and o.finance_updated_at == o.source_updated_at
    assert {c[0] for c in env.shop.calls} <= {"orders_page", "order", "transactions", "variant"}  # reads only
    assert r.status == "ok"
    env.shop.calls.clear()
    r = await env.pull()
    assert r.finance_refreshed == 0 and not [c for c in env.shop.calls if c[0] == "order"]  # nothing left to fill


async def test_a_failed_transactions_read_fails_the_order_and_keeps_the_cursor(env):  # noqa: F811
    before = (await env.snap()).cursor_updated_at
    env.shop.orders = [financed()]
    env.shop.fail["transactions"] = ShopifyError("the store's API rate limit was reached; the next cycle carries on")
    r = await env.pull()
    assert (r.failed, r.status) == (1, "not_advanced") and (await env.snap()).cursor_updated_at == before
    assert await env.q("SELECT 1 FROM sales_orders") == []  # the order's unit rolled back whole
    del env.shop.fail["transactions"]
    env.shop.transactions_by_order["1"] = [SALE]
    assert (await env.pull()).created == 1


async def test_the_detail_and_the_list_show_payments_refunds_net_and_the_refund_views(env):  # noqa: F811
    env.shop.orders = [financed(refunds=[REFUND], financial="partially_refunded"), financed(2, 9302)]
    env.shop.transactions_by_order["1"] = [SALE, REFUND_TX]
    env.shop.transactions_by_order["2"] = [SALE | {"id": 7101}]
    await env.pull()
    o, _ = await env.order(1)
    async with env.factory(OP) as u:
        d = await OrderService(u).detail(o.id)
        meta = await OrderService(u).meta()
    assert [(p.kind, p.amount) for p in d.payments] == [("sale", Decimal("100.00")), ("refund", Decimal("37.00"))]
    [rf] = d.refunds
    assert (rf.note, [ln.quantity for ln in rf.lines], [a.kind for a in rf.adjustments]) == ("damaged in transit", [1], ["shipping_refund"])
    assert (d.source_name, d.tags, d.payment_gateways, d.discount_codes[0]["code"]) == ("web", "wholesale, vip", ["shopify_payments"], "WELCOME5")
    assert "payment_details" not in d.model_dump_json() and "4242" not in d.model_dump_json()
    views = {v["key"]: v["state"]["filters"] for v in meta["views"] if v["key"] in ("refunded", "partially_refunded")}
    assert views == {"refunded": [{"field": "shopify_financial_status", "op": "eq", "value": "refunded"}],
                     "partially_refunded": [{"field": "shopify_financial_status", "op": "eq", "value": "partially_refunded"}]}
    labels = {f["name"]: f["label"] for f in meta["fields"]}
    assert (labels["source_name"], labels["tags"], labels["net_amount"], labels["total_refunded"], labels["shopify_financial_status"]) == (
        "Sales channel", "Tags", "Net", "Refunded", "Payment status")
    async with env.factory(OP) as u:
        p = await OrderRepository(u).list(QuerySpec())
        part = await OrderRepository(u).list(QuerySpec(filters=[parse_filter("shopify_financial_status:eq:partially_refunded")]))
        tagged = await OrderRepository(u).list(QuerySpec(search="vip"))
    assert [(t.currency, t.sums["net_amount"], t.sums["total_refunded"]) for t in p.totals] == [("CAD", "189.00", "37.00")]
    assert [x.source_order_id for x in part.items] == ["1"]
    assert sorted(x.source_order_id for x in tagged.items) == ["1", "2"]  # the list search finds tags
