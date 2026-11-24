"""The security log (≈ ABP SecurityLog, user 2026-11-23): a refusal is a sales_log row (step "security") with the
person, the permission, the route and the request id, kept although the refused request rolls back; the startup
line names the switches' values and the secrets set — never a secret's value, at any level."""
import json
import logging
from types import SimpleNamespace

from app.services.security_log import log_startup, switches
from tests.test_order_payment import OPERATOR, SETTLE_INPUT, _orders, act, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


async def test_a_refusal_is_logged_with_who_what_where_and_the_request_id(api):  # noqa: F811
    o = (await _orders(api, 9901))["9901"]
    r = await act(api, "settle", o, {**OPERATOR, "X-Request-Id": "sec-1"}, input=SETTLE_INPUT)
    assert r.status_code == 403
    [row] = await api.env.q("SELECT level, message, context FROM sales_log WHERE step = 'security'")
    c = row["context"]
    assert row["level"] == "WARNING" and "needs sales.settle" in row["message"]
    assert (c["permission"], c["role"], c["route"], c["request_id"]) == ("sales.settle", "op", "/api/v1/orders/actions/{action_id}", "sec-1")
    # admitted: no security row
    r = await act(api, "settle", o, input=SETTLE_INPUT)
    assert r.status_code == 200
    assert len(await api.env.q("SELECT id FROM sales_log WHERE step = 'security'")) == 1


async def test_startup_names_the_switches_and_the_secrets_set_never_a_secret_value(api, caplog, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SALES_SECRET_TEST_GUARD", "s3cr3t-value-never-logged")
    st = SimpleNamespace(SALES_PARTNER_WRITES_ENABLED=True, SALES_EXTERNAL_EMAIL_ENABLED=False, SALES_SCHEDULER_ENABLED=True)
    line = switches(st)
    assert line["SALES_PARTNER_WRITES_ENABLED"] is True and "SALES_SECRET_TEST_GUARD" in line["secrets_set"]
    caplog.set_level(logging.DEBUG)
    await log_startup(st, api.env.factory.session_factory)
    [row] = await api.env.q("SELECT message, context FROM sales_log WHERE step = 'security'")
    everything = json.dumps(line) + row["message"] + json.dumps(row["context"]) + "\n".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t-value-never-logged" not in everything and "SALES_SECRET_TEST_GUARD" in row["message"]
