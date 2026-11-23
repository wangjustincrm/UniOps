"""The SQL audit log store (sales36): a write saves its header (≈ AbpAuditLogs) and its actions (≈ AbpAuditLogActions)
in a session of its own; its entity changes (sales_audit) and its log rows (sales_log) carry the header's id; a failed
request's header is kept although the request rolled back."""
import pytest

from app.platform.auditing import NullAuditingStore, use_store
from app.services.audit_logs import SqlAuditingStore
from tests.test_order_payment import FINANCE, SETTLE_INPUT, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


@pytest.fixture
def sql_store(api):  # noqa: F811
    use_store(SqlAuditingStore(api.env.factory.session_factory))
    yield
    use_store(NullAuditingStore())


async def test_a_write_saves_its_header_actions_and_links_its_changes_and_log(api, sql_store):  # noqa: F811
    o = (await _orders(api, 9961))["9961"]
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=SETTLE_INPUT,
                       headers={**FINANCE, "X-Correlation-Id": "store-1"})
    assert r.status_code == 200
    [h] = await api.env.q("SELECT id, kind, route, http_status, role, entity_changes FROM sales_audit_logs WHERE correlation_id = 'store-1'")
    assert (h["kind"], h["route"], h["http_status"], h["role"]) == ("request", "/api/v1/orders/{id}/settle", 200, "finance")
    [a] = await api.env.q("SELECT service_name, method_name, parameters FROM sales_audit_log_actions WHERE audit_log_id = :i", i=h["id"])
    assert (a["service_name"], a["method_name"], a["parameters"]) == ("OrderPaymentService", "settle", SETTLE_INPUT)
    changes = await api.env.q("SELECT id FROM sales_audit WHERE audit_log_id = :i", i=h["id"])
    assert len(changes) == h["entity_changes"] >= 1
    [lg] = await api.env.q("SELECT step FROM sales_log WHERE audit_log_id = :i", i=h["id"])
    assert lg["step"] == "payment"


async def test_a_failed_request_keeps_its_header_and_a_plain_get_saves_none(api, sql_store):  # noqa: F811
    o = (await _orders(api, 9962))["9962"]
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version=999", json=SETTLE_INPUT, headers={**FINANCE, "X-Correlation-Id": "store-2"})
    assert r.status_code == 409
    [h] = await api.env.q("SELECT http_status, error_code, exceptions FROM sales_audit_logs WHERE correlation_id = 'store-2'")
    assert (h["http_status"], h["error_code"]) == (409, "entity.stale") and h["exceptions"].startswith("entity.stale:")
    await api.get(f"/api/v1/orders/{o['id']}", headers={**FINANCE, "X-Correlation-Id": "store-3"})
    assert await api.env.q("SELECT id FROM sales_audit_logs WHERE correlation_id = 'store-3'") == []
