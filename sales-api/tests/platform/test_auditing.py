"""Auditing (≈ ABP's Volo.Abp.Auditing, user 2026-11-23), against an in-memory store (the module's SQL store comes with
its migration): which requests and background scopes are saved (ABP's rules + ours), what one holds (the person, the
route, the status, the correlation id, the actions with their parameters whole, the entity changes, the error), the
correlation headers, contributors and exception subscribers."""
import logging

import pytest

from app.platform import correlation
from app.platform.auditing import (
    AuditLogContributor, AuditLogInfo, InMemoryAuditingStore, add_contributor, begin_scope, current_scope, should_save, use_store,
)
from app.platform.auditing.contributors import _contributors
from app.platform.auditing.store import NullAuditingStore
from app.platform.exceptions import ExceptionNotification, ExceptionSubscriber, add_exception_subscriber, notify, _subscribers
from app.platform.options import AuditingOptions, reset
from tests.test_order_payment import FINANCE, SETTLE_INPUT, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture


@pytest.fixture
def store():
    s = InMemoryAuditingStore()
    use_store(s)
    yield s
    use_store(NullAuditingStore())
    reset()


def test_the_rules_abp_s_defaults_and_ours():
    o = AuditingOptions(ignored_routes=("/health*",), always_log_routes=("/api/v1/*/export.csv",))
    req = lambda **k: AuditLogInfo(kind="request", user_id="u", **k)  # noqa: E731
    assert should_save(req(http_method="POST", route="/api/v1/orders/{id}/settle", http_status=200), o)
    assert not should_save(req(http_method="GET", route="/api/v1/orders", http_status=200), o)       # GET: no (ABP)
    assert should_save(req(http_method="GET", route="/api/v1/orders/{id}", http_status=404), o)       # …unless it fails
    assert should_save(req(http_method="GET", route="/api/v1/orders/export.csv", http_status=200), o)  # bulk data out
    assert not should_save(req(http_method="GET", route="/health", http_status=500), o)              # the probe: never
    assert should_save(req(http_method="GET", route="/x", http_status=200, forced=True), o)            # @audited
    assert not should_save(req(http_method="POST", route="/x", http_status=200, forced=False), o)      # @disable_auditing
    bg = AuditLogInfo(kind="job")
    assert not should_save(bg, o)  # a background scope that did nothing: not saved (ABP saves none at all)
    bg.entity_changes = 2
    assert should_save(bg, o)
    assert not should_save(bg, AuditingOptions(is_enabled=False))


async def test_a_write_is_saved_with_who_where_its_actions_parameters_changes_and_ids(api, store):  # noqa: F811
    o = (await _orders(api, 9951))["9951"]
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=SETTLE_INPUT,
                       headers={**FINANCE, "X-Correlation-Id": "corr-1"})
    assert r.status_code == 200 and r.headers["x-correlation-id"] == "corr-1" and r.headers["x-request-id"] == "corr-1"
    [log] = [x for x in store.logs if x.correlation_id == "corr-1"]
    assert (log.kind, log.http_method, log.route, log.http_status, log.role) == ("request", "POST", "/api/v1/orders/{id}/settle", 200, "finance")
    assert [(a.service_name, a.method_name) for a in log.actions] == [("OrderPaymentService", "settle")]
    assert log.actions[0].parameters == SETTLE_INPUT  # whole (the user's rule)
    assert log.entity_changes >= 1 and log.user_name and log.execution_duration_ms is not None
    # a GET that works: not saved; the old header still honoured
    r = await api.get(f"/api/v1/orders/{o['id']}", headers={**FINANCE, "X-Request-Id": "req-2"})
    assert r.headers["x-request-id"] == "req-2" and not [x for x in store.logs if x.correlation_id == "req-2"]


async def test_a_refusal_and_a_business_error_land_on_the_audit_log(api, store, caplog):  # noqa: F811
    from tests.test_order_payment import OPERATOR
    caplog.set_level(logging.WARNING)
    o = (await _orders(api, 9952))["9952"]
    await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=SETTLE_INPUT, headers={**OPERATOR, "X-Correlation-Id": "c-403"})
    await api.post(f"/api/v1/orders/{o['id']}/settle?version=999", json=SETTLE_INPUT, headers={**FINANCE, "X-Correlation-Id": "c-409"})
    logs = {x.correlation_id: x for x in store.logs}
    assert (logs["c-403"].http_status, logs["c-403"].error_code) == (403, "auth.forbidden")
    assert (logs["c-409"].http_status, logs["c-409"].error_code) == (409, "entity.stale")
    assert logs["c-409"].exceptions and logs["c-409"].exceptions[0].startswith("entity.stale:")
    assert any(r.levelno == logging.WARNING and "entity.stale" in r.getMessage() for r in caplog.records)  # ≈ IHasLogLevel


async def test_background_scopes_contributors_and_subscribers(store):
    seen = []

    class Tag(AuditLogContributor):
        def post_contribute(self, scope):
            scope.log.extra["tag"] = "x"
            seen.append(scope.log.kind)

    class Boom(ExceptionSubscriber):
        async def handle(self, n):
            raise RuntimeError("a subscriber that fails")
    add_contributor(Tag())
    add_exception_subscriber(Boom())
    try:
        async with begin_scope("job", "system") as s:
            assert current_scope() is s and correlation.current() == s.log.correlation_id
        async with begin_scope("outbox", "send 940", correlation_id="from-request") as s:
            s.entity_changed()
        await notify(ExceptionNotification(ValueError("x"), 409, "x.y", "msg"))  # a failing subscriber is ignored
    finally:
        _contributors[:] = [c for c in _contributors if not isinstance(c, Tag)]
        _subscribers[:] = [x for x in _subscribers if not isinstance(x, Boom)]
    assert seen == ["job", "outbox"]
    assert [(x.kind, x.correlation_id, x.extra) for x in store.logs] == [("outbox", "from-request", {"tag": "x"})]  # no-op job: not saved


async def test_a_failing_background_scope_is_saved_with_its_stack(store):
    with pytest.raises(RuntimeError):
        async with begin_scope("job", "boom"):
            raise RuntimeError("kaboom")
    [log] = store.logs
    assert "RuntimeError: kaboom" in log.exceptions[0]
