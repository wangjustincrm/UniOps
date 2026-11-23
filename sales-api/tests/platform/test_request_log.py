"""The request log (≈ ABP AuditLog, user 2026-11-23): one JSON line per request — its id, the person, the route
template, status, time, IP, the error — never the body or the query; the id comes back in X-Request-Id, is in the
oplog's context and is a 500's correlation id."""
import json
import logging

from tests.test_order_payment import FINANCE, SETTLE_INPUT, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


def _lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "sales.request"]


async def test_one_line_per_request_with_its_id_person_and_route_never_the_query(api, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, logger="sales.request")
    o = (await _orders(api, 9801))["9801"]
    r = await api.get(f"/api/v1/orders/{o['id']}?search=Jane%20Doe", headers={**FINANCE, "X-Request-Id": "abc-123"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "abc-123"
    [line] = [x for x in _lines(caplog) if x["request_id"] == "abc-123"]
    assert (line["method"], line["route"], line["status"], line["role"]) == ("GET", "/api/v1/orders/{id}", 200, "finance")
    assert line["user_name"] and line["duration_ms"] >= 0 and line["error"] is None
    assert "Jane" not in json.dumps(line)  # no query string, no body
    # a caller's id that is not a plain token is replaced; a business error is named by its code
    r = await api.post(f"/api/v1/orders/{o['id']}/settle", json=SETTLE_INPUT, headers={**FINANCE, "X-Request-Id": "bad id\n"})
    rid = r.headers["x-request-id"]
    assert rid != "bad id\n" and len(rid) == 32
    [line] = [x for x in _lines(caplog) if x["request_id"] == rid]
    assert (line["status"], line["error"]) == (422, "entity.version_required")


async def test_the_request_id_is_in_the_oplog_context(api):  # noqa: F811
    o = (await _orders(api, 9802))["9802"]
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=SETTLE_INPUT,
                       headers={**FINANCE, "X-Request-Id": "req-oplog-1"})
    assert r.status_code == 200
    [row] = await api.env.q("SELECT context FROM sales_log WHERE step = 'payment'")
    assert row["context"]["request_id"] == "req-oplog-1"


async def test_a_500_s_correlation_id_is_the_request_id(api, caplog):  # noqa: F811
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async def boom():
        raise RuntimeError("kaboom")
    app.add_api_route("/__test_boom", boom)
    try:
        caplog.set_level(logging.INFO, logger="sales.request")
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
            r = await c.get("/__test_boom", headers={"X-Request-Id": "boom-1"})
        assert r.status_code == 500 and r.json()["error"]["details"]["correlation_id"] == "boom-1"
        [line] = [x for x in _lines(caplog) if x["request_id"] == "boom-1"]
        assert (line["status"], line["error"]) == (500, "RuntimeError")
    finally:
        app.router.routes = [x for x in app.router.routes if getattr(x, "path", None) != "/__test_boom"]
