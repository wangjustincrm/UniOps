"""Every field of a list's rows can be a column (user 2026-11-18): /meta marks what each row carries
(`in_row`) and the amounts of money (`money` + the row's currency field), declared per list, never guessed.
The gate: for every list the Sales UI shows, what /meta offers as a column is in the read schema the
list returns, and a filter-only key is never offered (it would be an empty column)."""
from decimal import Decimal

import pytest

from app.platform.repository import Repository
from app.services.order_views import OrderService
from app.services.payment_list import PaymentRowService
from app.services.settlement_list import SettlementRowService
from app.services.stock import StockItemService, StockLotService, StockMoveService
from app.services.views import LISTS
from app.platform.query import QuerySpec
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture

SERVICES = {**LISTS, "payments": PaymentRowService, "settlements": SettlementRowService, "inventory/items": StockItemService,
            "inventory/lots": StockLotService, "inventory/moves": StockMoveService}


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_field_offered_as_a_column_is_in_the_row(name):
    svc = SERVICES[name]
    schema, repo = svc.read_schema, svc.repo_cls
    fields = repo.field_meta(schema)
    for f in fields:
        assert f["in_row"] == (f["name"] in schema.model_fields), (name, f["name"])
        if f.get("money"):
            assert f["currency_field"] in schema.model_fields, (name, f["name"])
    assert not {f["name"] for f in fields} & set(repo.meta_hidden), name
    assert set(repo.money_fields) <= {f["name"] for f in fields if f["in_row"]}, (name, "a money field the row does not carry")


def test_money_needs_a_currency_field_and_is_declared_not_guessed():
    with pytest.raises(TypeError, match="no currency field"):
        class _NoCurrency(Repository):  # noqa: F841
            model = object
            money_fields = ("total",)
    fields = {f["name"]: f for f in OrderService.repo_cls.field_meta(OrderService.read_schema)}
    assert (fields["net_amount"]["money"], fields["net_amount"]["currency_field"]) == (True, "currency")
    assert "money" not in fields["order_number"]
    assert "connection_id" not in fields and fields["connection_name"]["in_row"]


async def test_the_orders_rows_carry_the_source_amounts_and_the_connection_by_name(env):  # noqa: F811
    env.shop.orders = [shopify_order(1, 9301) | {"total_discounts": "3.94", "total_shipping_price_set": {"shop_money": {"amount": "10.00"}}}]
    await env.pull()
    async with env.factory(OP) as u:
        [o] = (await OrderService(u).list(QuerySpec())).items
    assert (o.subtotal, o.tax, o.connection_name) == (Decimal("90.00"), Decimal("10.00"), "store")
    assert o.discount is not None and o.shipping is not None


async def test_the_csv_follows_the_view_columns_labels_currency_and_refuses_what_the_row_lacks(env):  # noqa: F811
    from app.platform.errors import InvalidInput
    env.shop.orders = [shopify_order(1, 9301) | {"tags": "=HYPERLINK(\"x\")"}]
    await env.pull()
    async with env.factory(OP) as u:
        svc = OrderService(u)
        text = (await svc.export_csv(QuerySpec(), ["source_reference", "subtotal", "connection_name", "tags"])).decode("utf-8-sig")
        full = (await svc.export_csv(QuerySpec())).decode("utf-8-sig")  # no columns: the list's own full export, unchanged
        for bad in (["source_reference", "shadow"], ["connection_id"], ["nope"]):  # meta-hidden, hidden, unknown
            try:
                await svc.export_csv(QuerySpec(), bad)
                raise AssertionError(bad)
            except InvalidInput as e:
                assert e.code == "export.unknown_column"
    head, row = text.splitlines()[:2]
    assert head == "Order ref,Subtotal,Currency,Connection,Tags"  # the amount's currency beside it, once
    assert row.split(",")[:4] == [row.split(",")[0], "90.00", "CAD", "store"] and row.split(",")[0].endswith("9301")
    assert "'=HYPERLINK" in row                                    # the formula guard stays
    assert full.splitlines()[0].startswith("order_number,")


def test_amounts_are_totalled_unless_repeated_per_row():
    from app.services.order_views import OrderRepository
    from app.services.payment_list import PaymentRowRepository
    assert {"subtotal", "tax", "shipping", "discount", "total_amount"} <= set(OrderRepository.totalled)
    assert set(PaymentRowRepository.totalled) == {"sales_amount", "refund_amount", "net_amount"}  # order values repeat per payment
    assert all(f in r.sortable for r in (OrderRepository, PaymentRowRepository) for f in r.totalled)


async def test_an_added_amount_has_its_total_per_currency(env):  # noqa: F811
    env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302)]
    await env.pull()
    async with env.factory(OP) as u:
        page = await OrderService(u).list(QuerySpec())
    [t] = page.totals
    assert (t.currency, t.sums["subtotal"], t.sums["tax"]) == ("CAD", "180.00", "20.00")


async def test_a_csv_export_opens_in_excel_as_utf8(env):  # noqa: F811
    env.shop.orders = [shopify_order(1, 9301)]
    await env.pull()
    async with env.factory(OP) as u:
        for data in (await OrderService(u).export_csv(QuerySpec(), ["source_reference"]), await OrderService(u).export_csv(QuerySpec())):
            assert data[:3] == b"\xef\xbb\xbf"  # the BOM; the import reads it (utf-8-sig)


from tests.test_operations_api import READER  # noqa: E402
from tests.test_operations_api import api as ops_api  # noqa: E402,F401 — a fixture


async def test_every_csv_export_route_writes_the_bom(ops_api):  # noqa: F811
    """One writer for every export (platform csv_bytes): the next export cannot forget the BOM or the guard."""
    from app.main import app
    paths = sorted({r.path for r in app.routes if getattr(r, "path", "").endswith("/export.csv") and "{" not in r.path})
    assert len(paths) >= 10, paths
    for p in paths:
        r = await ops_api.get(p, headers=READER)
        assert r.status_code == 200, (p, r.status_code, r.text[:200])
        assert r.content[:3] == b"\xef\xbb\xbf", p
        meta = (await ops_api.get(p.removesuffix("export.csv") + "meta", headers=READER)).json()
        cols = [f["name"] for f in meta["fields"] if f["in_row"]][:3]  # the visible columns way, on every export
        r = await ops_api.get(f"{p}?columns={','.join(cols)}", headers=READER)
        assert r.status_code == 200 and r.content[:3] == b"\xef\xbb\xbf", (p, r.status_code, r.text[:200])
