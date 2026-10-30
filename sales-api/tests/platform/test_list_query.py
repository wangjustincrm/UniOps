"""The list query contract (design §13.19): typed operators per field kind,
several values (OR; none-of for the negative operators), AND across filters,
casts → 422, escaped LIKE, the whitelist, and the field metadata (/meta)."""
from decimal import Decimal

import pytest
import pytest_asyncio

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import InvalidInput
from app.platform.query import QuerySpec, parse_filter, split_values
from app.platform.repository import Repository
from tests.platform.conftest import Widget, WidgetRead


class Widgets(Repository[Widget]):
    model = Widget
    search_fields = ("code", "name")
    list_fields = ("code", "name", "email", "price", "active", "created_at")
    labels = {"email": "E-mail"}
    enums = {"code": ("A1", "B2", "C3", "N4")}
    default_sort = ("code",)


ROWS = [("A1", "Apple", "a@x.example", "3.00", True), ("B2", "Banana", None, "1.50", True),
        ("C3", "Cherry_pie", "c%@x.example", "9.99", False), ("N4", "banana split", "n@y.example", "0.00", True)]


@pytest_asyncio.fixture
async def seeded(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        for code, name, email, price, active in ROWS:
            uow.session.add(Widget(code=code, name=name, email=email, price=Decimal(price), active=active))
        await uow.commit()


async def codes(uow_factory, *raw: str) -> list[str]:
    async with uow_factory(SYSTEM_ACTOR) as uow:
        page = await Widgets(uow).list(QuerySpec(filters=[parse_filter(r) for r in raw], page_size=50))
        return [w.code for w in page.items]


@pytest.mark.parametrize("raw, expected", [
    # text
    ("name:contains:an", ["B2", "N4"]),                      # case-insensitive
    ("name:contains:APP|cherry", ["A1", "C3"]),               # several values: any of them
    ("name:not_contains:an|pie", ["A1"]),                     # none of them
    ("email:not_contains:x.example", ["B2", "N4"]),           # an empty value contains nothing
    ("name:starts_with:ban", ["B2", "N4"]),
    ("name:ends_with:_PIE", ["C3"]),                          # _ is literal
    ("email:contains:%", ["C3"]),                             # % is literal
    ("name:eq:Banana", ["B2"]),
    ("name:eq:banana", []),                                   # equals is exact
    ("name:eq:Apple|Banana", ["A1", "B2"]),
    ("email:ne:a@x.example", ["B2", "C3", "N4"]),             # NULL is not equal to it either
    ("email:is_null:", ["B2"]),
    ("email:not_null:", ["A1", "C3", "N4"]),
    ("name:in:Apple|Cherry_pie", ["A1", "C3"]),
    ("name:not_in:Apple|Cherry_pie", ["B2", "N4"]),
    # number
    ("price:eq:1.5", ["B2"]),
    ("price:ne:1.50|3", ["C3", "N4"]),
    ("price:gt:1.5", ["A1", "C3"]),
    ("price:gte:1.5", ["A1", "B2", "C3"]),
    ("price:lt:1.5", ["N4"]),
    ("price:lte:1.5", ["B2", "N4"]),
    ("price:between:1..5", ["A1", "B2"]),
    ("price:between:..1", ["N4"]),
    ("price:range:5..", ["C3"]),                              # the old name still works
    ("price:in:0|9.99", ["C3", "N4"]),
    ("price:not_in:0|9.99", ["A1", "B2"]),
    # bool, enum, datetime
    ("active:eq:false", ["C3"]),
    ("active:ne:false", ["A1", "B2", "N4"]),
    ("code:in:A1|N4", ["A1", "N4"]),
    ("code:not_in:A1|N4", ["B2", "C3"]),
    ("created_at:gte:2000-01-01T00:00:00+00:00", ["A1", "B2", "C3", "N4"]),
    ("created_at:is_null:", []),
    ("created_at:gte:2000-01-01", ["A1", "B2", "C3", "N4"]),            # no zone: UTC, never a DB error
    ("created_at:between:2000-01-01T00:00..2001-01-01", []),
])
async def test_every_operator_per_field_kind(uow_factory, seeded, raw, expected):
    assert await codes(uow_factory, raw) == expected


async def test_different_filters_are_and_also_on_one_field(uow_factory, seeded):
    assert await codes(uow_factory, "name:contains:an", "active:eq:true", "price:gt:1") == ["B2"]
    assert await codes(uow_factory, "price:gte:1", "price:lte:3") == ["A1", "B2"]


@pytest.mark.parametrize("raw, code", [
    ("price:eq:abc", "query.bad_value"),                      # a bad cast is a 422, never a DB error
    ("price:between:x..2", "query.bad_value"),
    ("created_at:gt:yesterday", "query.bad_value"),
    ("active:eq:maybe", "query.bad_value"),
    ("price:contains:1", "query.unsupported_operator"),       # text operators on a number
    ("code:contains:A", "query.unsupported_operator"),        # an enum: choose, never type
    ("active:gt:true", "query.unsupported_operator"),
    ("version:eq:1", "query.unknown_field"),                  # not on the whitelist
    ("historical:eq:true", "query.unknown_field"),
])
async def test_what_is_refused(uow_factory, seeded, raw, code):
    with pytest.raises(InvalidInput) as e:
        await codes(uow_factory, raw)
    assert e.value.code == code


def test_the_encoding():
    assert split_values(r"a|b\|c|\\d") == ["a", "b|c", "\\d"]
    assert parse_filter(r"name:eq:R\|D").value == "R|D"
    assert parse_filter("name:in:a").value == ["a"]
    assert parse_filter("name:is_null:").value is None
    assert parse_filter("price:between:1..").value == ["1", None]
    for raw, code in (("price:gt:1|2", "query.bad_filter"), ("name:eq:", "query.bad_filter"),
                      ("name:like:x", "query.unsupported_operator"), ("name:in:" + "|".join("x" * 101), "query.too_many_values"),
                      ("name:eq:" + "x" * 201, "query.value_too_long"), ("price:between:5", "query.bad_filter")):
        with pytest.raises(InvalidInput) as e:
            parse_filter(raw)
        assert e.value.code == code, raw


def test_field_meta_comes_from_the_declaration():
    meta = {f["name"]: f for f in Widgets.field_meta(WidgetRead)}
    assert list(meta)[:6] == ["id", "code", "name", "email", "price", "active"]  # the read schema's order first
    assert meta["code"] | {"operators": None} == {
        "name": "code", "label": "Code", "type": "enum", "enum_values": ["A1", "B2", "C3", "N4"], "filterable": True,
        "sortable": True, "groupable": False, "operators": None}
    assert meta["code"]["operators"] == ["eq", "ne", "in", "not_in", "is_null", "not_null"]
    assert (meta["email"]["label"], meta["email"]["type"]) == ("E-mail", "text")
    assert "starts_with" in meta["email"]["operators"]
    assert (meta["price"]["type"], meta["active"]["type"], meta["created_at"]["type"]) == ("number", "bool", "datetime")
    assert "between" in meta["price"]["operators"] and "contains" not in meta["price"]["operators"]
    assert (meta["id"]["filterable"], meta["id"]["sortable"], meta["id"]["operators"]) == (False, False, [])
    assert meta["version"]["label"] == "Version"


def test_every_listed_resource_names_each_field_once_and_keeps_its_schema_fields():
    from app.services.connections import ConnectionService
    from app.services.items import DcRoutingService, ItemService, PartnerCodeService
    from app.services.operations import DocumentService, LogService, RunService
    from app.services.order_views import OrderService
    from app.services.partners import PartnerService
    for svc in (PartnerService, ItemService, PartnerCodeService, DcRoutingService, ConnectionService, OrderService,
                DocumentService, RunService, LogService):
        names = [f["name"] for f in svc.repo_cls.field_meta(svc.read_schema)]
        assert len(names) == len(set(names)), svc.__name__
        for f in svc.repo_cls.field_meta(svc.read_schema):  # a declared enum is offered as one
            assert (f["type"] == "enum") == (f["name"] in svc.repo_cls.enums), (svc.__name__, f["name"])
    partner = {f["name"]: f for f in PartnerService.repo_cls.field_meta(PartnerService.read_schema)}
    assert partner["channel"]["enum_values"] == ["SHOPIFY", "TRUECOMMERCE", "MANUAL"] and partner["channel"]["filterable"]
