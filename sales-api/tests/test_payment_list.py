"""The Payments list (Finance, user 2026-11-16): one row per Shopify transaction (and one per
paid order with none); the method as one name, the gateway's own transaction id, the totals per
method / week / month, the issues behind the Exceptions view, and the system views that fit."""
from decimal import Decimal

from app.models.payment_list import ISSUES
from app.platform.query import QuerySpec, SortKey, parse_filter
from app.platform.views import check_state
from app.services.payment_list import PaymentRowRepository, PaymentRowService
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_shopify_finance import CARD

TX = {"kind": "sale", "status": "success", "currency": "CAD", "processed_at": "2026-10-29T08:01:00-04:00", "test": False,
      "payment_details": CARD, "receipt": {"transaction_id": "121004214042", "account_number": "XXXX4242"}}


def tx(id, **kw):
    return TX | {"id": id} | kw


def order(oid, *, financial="paid", total="100.00", tags=None, source="web", cancelled_at=None):
    return shopify_order(oid, 9300 + oid, financial=financial, cancelled_at=cancelled_at) | {
        "source_name": source, "tags": tags, "total_price": total, "billing_address": {"name": f"Customer {oid}"}}


async def rows(env, *filters, group=None):  # noqa: F811
    async with env.factory(OP) as u:
        return await PaymentRowRepository(u).list(QuerySpec(
            filters=[parse_filter(f) for f in filters], group=[SortKey(field=group)] if group else [], page_size=200))


async def test_the_list_rows_methods_ids_totals_and_issues(env):  # noqa: F811
    env.shop.orders = [
        order(1, financial="partially_refunded"), order(2), order(3, tags="seal_subsequent_order"),
        order(4, financial="partially_paid", total="25.00", source="shopify_draft_order"),
        order(5, financial="pending"), order(6, cancelled_at="2026-10-30T10:00:00-04:00"),
        order(7, financial="pending"), order(8, total="0.00")]
    env.shop.transactions_by_order |= {
        "1": [tx(101, gateway="authorize_net", amount="100.00", authorization="121004214042#2281#purchase", payment_id="r1"),
              tx(102, gateway="authorize_net", kind="refund", amount="30.00", parent_id=101, authorization="121004219999#2281#refund")],
        "2": [tx(201, gateway="authorize.net", amount="100.00", authorization=None, payment_id="rzyOx", receipt={"payment_id": "rzyOx"})],
        "3": [tx(301, gateway="paypal", amount="100.00", authorization="3TE482530G0661810", payment_id="ri90")],
        "5": [tx(501, gateway="AlphaPay", amount="100.00", status="pending", processed_at="2026-01-02T10:00:00-05:00")],
        "6": [tx(601, gateway="authorize.net", amount="100.00")],
        "7": [tx(701, gateway="authorize.net", amount="100.00", status="failure")],
    }
    await env.pull()
    page = await rows(env)
    ids = {x["shopify_id"]: x["id"] for x in await env.q("SELECT id, shopify_id FROM sales_order_payments")}
    by = {sid: r for r in page.items for sid, i in ids.items() if r.id == i}
    one, ref, new, pp = by[101], by[102], by[201], by[301]
    assert (one.method, one.gateway_ref, one.sales_amount, one.refund_amount, one.net_amount) == (
        "Authorize.Net", "121004214042", Decimal("100.00"), 0, Decimal("100.00"))
    assert (ref.kind, ref.gateway_ref, ref.refund_amount, ref.net_amount) == ("refund", "121004219999", Decimal("30.00"), Decimal("-30.00"))
    assert (new.method, new.gateway_ref, new.shopify_payment_id) == ("Authorize.Net", None, "rzyOx")  # Shopify's app: no processor id
    assert (pp.method, pp.gateway_ref, pp.order_source) == ("PayPal", "3TE482530G0661810", "Subscription")
    assert (one.order_source, one.customer, one.order_ref.endswith("-9301")) == ("One-off", "A B", True)  # the order's bill-to / ship-to name
    # an order paid with no transaction at all: its own row; a free one (0.00) is not an exception
    [none] = [r for r in page.items if r.kind == "none"]
    assert (none.issue, none.amount, none.order_id) == ("Partially paid", 0, none.order_id)
    assert {r.issue for r in page.items if r.issue} == {"Partially paid", "Pending over 7 days", "Cancelled, not fully refunded",
                                                          "Failed on an open order"} <= set(ISSUES)
    assert (by[501].issue, by[601].issue, by[701].issue) == ("Pending over 7 days", "Cancelled, not fully refunded", "Failed on an open order")
    assert [(t.currency, t.sums) for t in page.totals] == [
        ("CAD", {"sales_amount": "400.00", "refund_amount": "30.00", "net_amount": "370.00", "gateway_fee": "0",
                 "net_after_fee": "370.00"})]  # no gateway data here: no fee, Net after fee = Net
    # no card data in any row
    async with env.factory(OP) as u:
        served = (await PaymentRowService(u).list(QuerySpec(page_size=200))).model_dump_json()
    assert "4242" not in served and "account_number" not in served and "rzyOx" in served

    grouped = await rows(env, "status:eq:success", group="method")
    sums = {tuple(g.key): g.totals[0].sums["net_amount"] for g in grouped.groups}
    assert sums == {("Authorize.Net",): "270.00", ("PayPal",): "100.00"}
    week = await rows(env, "method:eq:PayPal", group="processed_week")
    assert [tuple(g.key) for g in week.groups] == [("2026-10-26",)]  # Monday of its week, Toronto
    assert [r.id for r in (await rows(env, "issue:not_null:")).items if r.kind == "none"] == [none.id]


async def test_the_system_views_fit_the_list_and_are_served(env):  # noqa: F811
    async with env.factory(OP) as u:
        meta = await PaymentRowService(u).meta()
    names = [v["name"] for v in meta["views"]]
    assert names == ["All", "Authorize.Net — transactions", "Authorize.Net — by settlement", "PayPal — weekly",
                     "AlphaPay / Manual — monthly", "Exceptions"]
    for v in meta["views"]:
        assert check_state(v["state"], PaymentRowRepository) == v["state"]
    assert PaymentRowRepository.unknown_view_columns() == []
