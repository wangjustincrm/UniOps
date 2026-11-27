"""Records and references (user 2026-11-27): a record with a page of its own is a type, declared by its list's
service; every list field that names such a record declares its Ref, sent in /meta, so the client links it; and
whether a link opens for this person is its type's list read_permission — one key for the list, the page and the link."""
import re

import pytest

from app.platform import record_types as rt
from app.platform.record_types import Ref, read_permission_of, record_types
from tests.test_list_resources import LISTS
from tests.test_orders import env  # noqa: F401 — env is a fixture
from tests.test_orders_api import NOBODY, READER, api  # noqa: F401 — api is a fixture

# A field named like this names a record of that type: it declares the Ref, unless it is the list's own key.
NAMED = {"item": r"item_code", "lot": r"lot", "order": r"order_id|order_ref|order_number|parent_order_id"}
OWN_KEY = {("OrderService", "order_number"), ("ItemService", "item_code")}
# The types to come with their pages (partner, invoice, transfer, document, …) are added to NAMED then.


def test_the_record_types_and_the_list_each_belongs_to():
    assert {t: s.__name__ for t, s in record_types().items()} == {"order": "OrderService", "item": "ItemService", "lot": "LotService"}
    assert read_permission_of("item") == "sales.read"


def test_a_second_list_for_a_type_is_refused():
    class Other:
        read_permission = "x"
    with pytest.raises(TypeError, match="record type 'item'"):
        rt.register("item", Other)
    assert record_types()["item"].__name__ == "ItemService"


@pytest.mark.parametrize("svc", LISTS, ids=lambda c: c.__name__)
def test_every_field_that_names_a_record_links_it(svc):
    """A new column named like a record's key fails here until it declares its Ref (or is the list's own key)."""
    fields = {f["name"]: f for f in svc.repo_cls.field_meta(svc.read_schema) if f["in_row"]}
    missing = [(n, t) for t, pat in NAMED.items() for n, f in fields.items()
               if re.fullmatch(pat, n) and (svc.__name__, n) not in OWN_KEY and (f.get("ref") or {}).get("type") != t]
    assert missing == []


@pytest.mark.parametrize("svc", [c for c in LISTS if c.repo_cls.refs], ids=lambda c: c.__name__)
def test_every_ref_is_a_known_type_and_its_key_is_in_the_row(svc):
    fields = {f["name"]: f for f in svc.repo_cls.field_meta(svc.read_schema)}
    for name, ref in svc.repo_cls.refs.items():
        assert ref.type in record_types(), (name, ref)
        assert fields[name]["in_row"] and fields[ref.key or name]["in_row"], (name, ref)
        assert fields[name]["ref"] == {"type": ref.type, "key": ref.key or name}


def test_a_detail_names_only_known_types_and_meta_says_them():
    from app.services.order_views import OrderService
    assert OrderService.repo_cls.detail_refs == ("item", "lot")
    for svc in LISTS:
        assert set(svc.repo_cls.detail_refs) <= set(record_types()), svc.__name__


async def test_orders_meta_says_the_types_its_detail_names(api):  # noqa: F811
    assert (await api.get("/api/v1/orders/meta", headers=READER)).json()["refs"] == {
        "item": {"readable": True}, "lot": {"readable": True}, "order": {"readable": True}}


def test_lists_with_refs():
    assert sum(1 for c in LISTS if c.repo_cls.refs) >= 9
    assert Ref("order", "order_id").as_dict("order_ref") == {"type": "order", "key": "order_id"}


async def test_meta_says_per_type_whether_this_person_may_read_it(api, monkeypatch):  # noqa: F811
    meta = (await api.get("/api/v1/lots/meta", headers=READER)).json()
    assert meta["refs"] == {"item": {"readable": True}, "lot": {"readable": True}, "order": {"readable": True}}
    assert next(f for f in meta["fields"] if f["name"] == "order_ref")["ref"] == {"type": "order", "key": "order_id"}
    assert "ref" not in next(f for f in meta["fields"] if f["name"] == "partner_name")
    assert (await api.get("/api/v1/config/carriers/meta", headers=READER)).json()["refs"] == {}
    # The item's own list key decides: without it, the link is not offered (the list itself still reads).
    item = record_types()["item"]
    monkeypatch.setattr(item, "read_permission", "sales.config")
    meta = (await api.get("/api/v1/lots/meta", headers=READER)).json()
    assert meta["refs"]["item"] == {"readable": False} and meta["refs"]["order"] == {"readable": True}
    assert (await api.get("/api/v1/lots/meta", headers=NOBODY)).status_code == 403
