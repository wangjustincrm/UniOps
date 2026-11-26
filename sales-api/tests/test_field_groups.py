"""The search panel's field groups and relative dates (#32, user 2026-11-26), and the list's count alone."""
from datetime import date, datetime, timezone

import pytest

from app.platform.clock import FixedClock, use_clock
from app.platform.errors import InvalidInput
from app.platform.query import Filter, QuerySpec
from app.platform.relative_dates import RELATIVE, period
from app.services.order_views import OrderService
from tests.test_list_resources import LISTS
from tests.test_orders import OP, env, shopify_order  # noqa: F401 — env is a fixture
from tests.test_orders_api import NOBODY, READER, api  # noqa: F401 — api is a fixture

GROUPED = [c for c in LISTS if c.repo_cls.field_groups]


def test_orders_is_grouped():
    assert OrderService in GROUPED


@pytest.mark.parametrize("svc", GROUPED, ids=[c.__name__ for c in GROUPED])
def test_every_field_offered_for_a_search_is_in_exactly_one_group_on_purpose(svc):
    """A new filterable field of a grouped list fails here until it is put in a group ("Other" too is named): groups never rot."""
    repo = svc.repo_cls
    offered = {f["name"] for f in repo.field_meta(svc.read_schema) if f["filterable"]}
    named = [n for _, names in repo.field_groups for n in names]
    assert sorted(n for n in set(named) if named.count(n) > 1) == []         # once
    assert sorted(offered - set(named)) == []                                  # each offered field is placed
    assert sorted(set(named) - offered) == []                                  # no group names what is not offered
    assert repo.group_labels()[-1] == "Other" and len(set(repo.group_labels())) == len(repo.group_labels())


@pytest.mark.parametrize("svc", [c for c in LISTS if not c.repo_cls.field_groups][:3])
def test_a_list_without_groups_has_everything_in_other(svc):
    assert {f["group"] for f in svc.repo_cls.field_meta(svc.read_schema)} <= {"Other"} and svc.repo_cls.group_labels() == ["Other"]


TODAY = date(2026, 11, 26)


@pytest.mark.parametrize(("token", "first", "last"), [
    ("today", TODAY, TODAY), ("last_7_days", date(2026, 11, 20), TODAY), ("last_30_days", date(2026, 10, 28), TODAY),
    ("this_month", date(2026, 11, 1), TODAY), ("last_month", date(2026, 10, 1), date(2026, 10, 31))])
def test_each_period(token, first, last):
    assert period(token, TODAY) == (first, last)


def test_last_month_in_january_and_this_month_on_the_first():
    assert period("last_month", date(2027, 1, 10)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert period("this_month", date(2026, 12, 1)) == (date(2026, 12, 1), date(2026, 12, 1))


async def _orders(env, days):  # noqa: F811
    """Orders received at these moments (UTC), their order date the same instant's UTC date."""
    env.shop.orders = [shopify_order(i + 1, 9301 + i) for i in range(len(days))]
    await env.pull()
    async with env.factory(OP) as u:
        from sqlalchemy import text
        for i, at in enumerate(days):
            await u.session.execute(text("UPDATE sales_orders SET first_received_at = :at, order_date = :d WHERE source_order_id = :s"),
                                    {"at": at, "d": at.date(), "s": str(i + 1)})
        await u.commit()


async def found(env, field, value):  # noqa: F811
    async with env.factory(OP) as u:
        page = await OrderService(u).list(QuerySpec(filters=[Filter(field=field, op="eq", value=value)], page_size=50))
        count = await OrderService(u).count(QuerySpec(filters=[Filter(field=field, op="eq", value=value)]))
    assert count == page.total
    return sorted(int(o.source_order_number) for o in page.items)


async def test_today_is_toronto_s_day_for_a_moment_and_for_a_date(env):  # noqa: F811
    # 2026-11-27 03:30 UTC is still the 26th in Toronto (EST); 2026-11-26 04:30 UTC is the 25th, 23:30 local
    await _orders(env, [datetime(2026, 11, 27, 3, 30, tzinfo=timezone.utc), datetime(2026, 11, 26, 4, 30, tzinfo=timezone.utc),
                        datetime(2026, 11, 26, 5, 30, tzinfo=timezone.utc), datetime(2026, 10, 15, 12, tzinfo=timezone.utc)])
    use_clock(FixedClock(datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)))  # 23:00 on the 26th in Toronto
    try:
        assert await found(env, "first_received_at", "today") == [9301, 9303]
        assert await found(env, "first_received_at", "last_7_days") == [9301, 9302, 9303]
        assert await found(env, "first_received_at", "last_month") == [9304]
        assert await found(env, "order_date", "today") == [9302, 9303]        # a date: its own day (here the UTC date stored)
        assert await found(env, "order_date", "this_month") == [9302, 9303]  # up to today: the 27th is not yet
        with pytest.raises(InvalidInput) as e:
            await found(env, "order_date", "last_week")
        assert e.value.code == "query.bad_value"
        assert await found(env, "partner_name", "today") == []  # a text field: "today" is only a word there
    finally:
        use_clock(None)


def test_a_saved_view_keeps_the_period_not_its_days():
    from app.platform.views import VIEW_STATE_VERSION, check_state
    state = {"version": VIEW_STATE_VERSION, "filters": [{"field": "order_date", "op": "eq", "value": "last_7_days"}]}
    assert check_state(state, OrderService.repo_cls)["filters"] == state["filters"]


async def test_count_alone_over_http_and_meta_says_groups_and_periods(api):  # noqa: F811
    api.env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302)]
    await api.env.pull()
    assert (await api.get("/api/v1/orders/count", headers=NOBODY)).status_code == 403
    r = await api.get("/api/v1/orders/count?filter=source_order_number:eq:9301", headers=READER)
    assert (r.status_code, r.json()) == (200, {"total": 1})
    assert (await api.get("/api/v1/orders/count", headers=READER)).json() == {"total": 2}
    assert (await api.get("/api/v1/orders/count?filter=order_date:eq:last_week", headers=READER)).status_code == 422
    meta = (await api.get("/api/v1/orders/meta", headers=READER)).json()
    assert meta["groups"][:2] == ["Status", "Order"] and meta["groups"][-1] == "Other"
    assert {f["name"]: f["group"] for f in meta["fields"]}["over_shipped_ea"] == "Shipments"
    assert [x["value"] for x in meta["relative_dates"]] == list(RELATIVE)
    assert {x["value"]: x["help"] for x in meta["relative_dates"]}["last_7_days"] == "Today and the 6 days before."
    assert (await api.get("/api/v1/invoices/count", headers=READER)).json() == {"total": 0}  # every list has it


async def test_count_equals_the_lists_own_total_for_every_view_and_some_searches(api):  # noqa: F811
    """"Search (42)" never disagrees with the page: the same query, every Orders preset, filters, search and periods."""
    import urllib.parse
    api.env.shop.orders = [shopify_order(i, 9300 + i) for i in range(1, 6)]
    await api.env.pull()
    meta = (await api.get("/api/v1/orders/meta", headers=READER)).json()

    def query(state):
        q = [("filter", f"{f['field']}:{f['op']}:{'|'.join(map(str, f['value'])) if isinstance(f.get('value'), list) else f.get('value', '')}")
             for f in state.get("filters", [])]
        return urllib.parse.urlencode(q)
    queries = [query(v["state"]) for v in meta["views"]] + [
        "search=930", "filter=order_date:eq:last_30_days", "filter=first_received_at:eq:today",
        "filter=source_order_number:in:9301|9302", "filter=applied_945s:gte:2", "search=nothing-matches"]
    for q in queries:
        page = (await api.get(f"/api/v1/orders?{q}&page_size=1", headers=READER)).json()
        count = (await api.get(f"/api/v1/orders/count?{q}", headers=READER)).json()
        assert count["total"] == page["total"], q
    assert len(queries) >= 10


def test_count_is_mounted_before_the_record_route_on_every_list_and_needs_its_permission():
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    for svc in LISTS:
        base = "/api/v1" + svc.resolved().path
        assert base + "/count" in paths, svc.__name__
        if base + "/{id}" in paths:
            assert paths.index(base + "/count") < paths.index(base + "/{id}"), svc.__name__


@pytest.mark.parametrize("path", ["/orders/count", "/invoices/count", "/config/partners/count", "/inventory/items/count"])
async def test_count_needs_the_lists_read_permission(api, path):  # noqa: F811
    assert (await api.get("/api/v1" + path, headers=NOBODY)).status_code == 403
    assert (await api.get("/api/v1" + path, headers=READER)).status_code == 200
