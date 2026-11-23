"""Operations → Audit log (sales36): the logs list, filter and open with their actions and entity changes; the entity
changes list by entity; sales.config only for now (the user, 2026-11-23)."""
import pytest

from app.platform.auditing import NullAuditingStore, use_store
from app.services.audit_logs import SqlAuditingStore
from tests.test_order_payment import FINANCE, OPERATOR, SETTLE_INPUT, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


@pytest.fixture
def sql_store(api):  # noqa: F811
    use_store(SqlAuditingStore(api.env.factory.session_factory))
    yield
    use_store(NullAuditingStore())


async def test_the_audit_log_lists_filters_and_opens_a_log_with_its_actions_and_changes(api, sql_store):  # noqa: F811
    o = (await _orders(api, 9971))["9971"]
    await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=SETTLE_INPUT, headers={**FINANCE, "X-Correlation-Id": "page-1"})
    await api.post(f"/api/v1/orders/{o['id']}/settle?version=999", json=SETTLE_INPUT, headers={**FINANCE, "X-Correlation-Id": "page-2"})
    assert (await api.get("/api/v1/audit-logs", headers=FINANCE)).status_code == 403  # sales.config only
    r = (await api.get("/api/v1/audit-logs?filter=correlation_id:in:page-1|page-2", headers=OPERATOR)).json()
    assert {x["correlation_id"] for x in r["items"]} == {"page-1", "page-2"}
    failed = (await api.get("/api/v1/audit-logs?filter=has_exception:eq:true&filter=correlation_id:in:page-1|page-2", headers=OPERATOR)).json()
    assert [(x["correlation_id"], x["http_status"], x["error_code"]) for x in failed["items"]] == [("page-2", 409, "entity.stale")]
    [ok] = [x for x in r["items"] if x["correlation_id"] == "page-1"]
    d = (await api.get(f"/api/v1/audit-logs/{ok['id']}/detail", headers=OPERATOR)).json()
    assert [(a["service_name"], a["method_name"]) for a in d["actions"]] == [("OrderPaymentService", "settle")]
    assert d["actions"][0]["parameters"] == SETTLE_INPUT and d["changes"] and d["changes"][0]["entity"] == "sales_orders"
    meta = (await api.get("/api/v1/audit-logs/meta", headers=OPERATOR)).json()
    assert {f["name"] for f in meta["fields"]} >= {"execution_time", "user_name", "route", "http_method", "http_status", "correlation_id"}
    ch = (await api.get(f"/api/v1/entity-changes?filter=entity:eq:sales_orders&filter=entity_id:eq:{o['id']}", headers=OPERATOR)).json()
    assert ch["items"] and all(c["entity_id"] == str(o["id"]) for c in ch["items"])
    assert any(c["audit_log_id"] == ok["id"] for c in ch["items"])
