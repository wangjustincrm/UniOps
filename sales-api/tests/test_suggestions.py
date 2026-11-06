"""Values of other records offered while typing a search (user 2026-11-06)."""
import pkgutil
from importlib import import_module

import pytest

import app.services
from app.platform.errors import InvalidInput
from app.platform.repository import Repository
from app.platform.query import QuerySpec  # noqa: F401
from app.services.order_views import OrderRepository, OrderService
from tests.test_orders import OP, env, po, run_850  # noqa: F401 — env is a fixture


def _all_repositories():
    for m in pkgutil.iter_modules(app.services.__path__):
        import_module(f"app.services.{m.name}")

    def walk(c):
        for s in c.__subclasses__():
            yield s
            yield from walk(s)
    return list(walk(Repository))


def test_no_personal_field_can_be_suggested():
    bad = ("email", "phone", "address", "ship_to", "bill_to", "contact", "customer", "notes", "city", "postal", "zip")
    found = [(r.__name__, f) for r in _all_repositories() for f in r.suggestable if any(b in f for b in bad)]
    assert found == []


async def test_the_values_in_the_list_most_used_first_and_narrowed_by_what_is_typed(env):  # noqa: F811
    line = [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}]
    await run_850(env, {"PurchaseOrder": [po("LBL", "PO-1", line), po("SOB", "PO-2", line), po("SOB", "PO-3", line)]})
    async with env.factory(OP) as u:
        svc = OrderService(u)
        assert await svc.suggest("partner_name") == ["Sobeys", "Loblaws"]
        assert await svc.suggest("partner_name", "lob") == ["Loblaws"]
        assert await svc.suggest("source_order_number", "%") == []  # a wildcard is a plain character


async def test_a_field_that_was_not_opened_is_refused(env):  # noqa: F811
    async with env.factory(OP) as u:
        with pytest.raises(InvalidInput) as e:
            await OrderService(u).suggest("review_reason")
    assert e.value.code == "query.not_suggestable"


def test_the_meta_says_which_fields_suggest():
    fields = {f["name"]: f for f in OrderRepository.field_meta()}
    assert fields["partner_name"]["suggestable"] is True and "suggestable" not in fields["review_reason"]
