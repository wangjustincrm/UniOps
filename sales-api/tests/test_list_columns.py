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
