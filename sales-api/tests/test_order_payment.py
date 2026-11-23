"""Payment (user 2026-11-19 / 2026-11-23): an order's Payment beside Fulfillment and Invoice; Settled is a finance
user's mark (sales.settle) with a date and a reference, for every source; its undo needs a reason; both are audited;
Mark settled in bulk gives one result per order. Finance only: an operator is refused, a finance user admitted.
The order's state (Completed) does not change."""
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import get_uow_factory
from app.domain.order_payment import DUE, NOT_NEEDED, NOT_SETTLED, OVERDUE, SETTLED, payment_status, terms_days
from app.main import app
from app.services.order_payment import ORDER_ACTIONS
from app.platform.actors import SYSTEM_ACTOR
from app.runtime import build_runtime, get_runtime
from tests.conftest import make_token
from tests.test_orders import env, shopify_order  # noqa: F401 — env is a fixture

GRANTS = {"finance": {"sales.read", "sales.settle"}, "op": {"sales.read", "sales.order.operate", "sales.invoice.manage",
                                                           "sales.config"}}
FINANCE, OPERATOR = ({"Authorization": f"Bearer {make_token(r)}"} for r in ("finance", "op"))
TODAY = date(2026, 11, 23)


def test_the_rule_and_the_terms():
    assert [terms_days(t) for t in ("Net 30", "NET30", " net 45 ", "2% 10 Net 30", "30 days", None, "")] == [
        30, 30, 45, None, None, None, None]  # only "Net N": terms in other words are never guessed
    rule = dict(draft=False, cancelled=False, settled=False, invoice_sent=False, invoice_date=None, terms=None, today=TODAY)
    assert payment_status(**rule) == NOT_SETTLED
    assert payment_status(**rule | {"draft": True}) is None
    assert payment_status(**rule | {"cancelled": True}) == NOT_NEEDED
    assert payment_status(**rule | {"cancelled": True, "settled": True}) == SETTLED
    sent = rule | {"invoice_sent": True, "invoice_date": TODAY - timedelta(days=30)}
    assert payment_status(**sent) == NOT_SETTLED  # invoiced, no terms: plain Not settled
    assert payment_status(**sent | {"terms": "Net 30"}) == DUE          # due today
    assert payment_status(**sent | {"terms": "Net 29"}) == OVERDUE
    assert payment_status(**sent | {"cancelled": True, "terms": "Net 29"}) == OVERDUE  # invoiced, then cancelled


@pytest_asyncio.fixture
async def api(env):  # noqa: F811
    async def matrix(session, actor, key):  # noqa: ARG001
        return key in GRANTS.get(actor.role or "", set())
    env.factory.permissions = matrix
    runtime = build_runtime(settings, env.factory, shopify=env.shop)
    app.dependency_overrides[get_uow_factory] = lambda: env.factory
    app.dependency_overrides[get_runtime] = lambda: runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.env = env
        yield c
    app.dependency_overrides.clear()


async def _orders(api, *numbers):
    api.env.shop.orders = [shopify_order(i, n) for i, n in enumerate(numbers, start=1)]
    await api.env.pull()
    return {r["source_order_number"]: r for r in await api.env.q(
        "SELECT id, source_order_number, version FROM sales_orders ORDER BY id")}


async def _exec(api, sql):
    async with api.env.factory(SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text(sql))
        await u.commit()


async def _invoice(api, order_id, *, days_ago, terms):
    await _exec(api, 
        "INSERT INTO sales_invoices (invoice_number, order_id, method, invoice_date, currency, payment_terms, subtotal, total,"
        " sent_at, version, created_by_name) VALUES "
        f"('INV-{order_id}', {order_id}, '810_JSON', (now() AT TIME ZONE 'America/Toronto')::date - {days_ago}, 'CAD',"
        f" {terms!r}, 10, 10, now(), 1, 'test')".replace("None", "NULL"))


async def _status(api, oid):
    r = await api.get(f"/api/v1/orders/{oid}", headers=FINANCE)
    assert r.status_code == 200, r.text
    return r.json()


async def test_payment_shows_beside_the_status_lists_filters_and_follows_the_terms(api):
    o = await _orders(api, 9601, 9602, 9603, 9604)
    await _invoice(api, o["9602"]["id"], days_ago=10, terms="Net 30")
    await _invoice(api, o["9603"]["id"], days_ago=31, terms="Net 30")
    await _invoice(api, o["9604"]["id"], days_ago=31, terms=None)  # the partner has no terms either: never guessed
    assert [(await _status(api, o[n]["id"]))["payment_status"] for n in ("9601", "9602", "9603", "9604")] == [
        NOT_SETTLED, DUE, OVERDUE, NOT_SETTLED]
    r = (await api.get("/api/v1/orders?filter=payment_status:in:Due|Overdue&sort=payment_status", headers=OPERATOR)).json()
    assert [x["source_order_number"] for x in r["items"]] == ["9602", "9603"]  # every reader sees it
    meta = (await api.get("/api/v1/orders/meta", headers=OPERATOR)).json()
    field = next(f for f in meta["fields"] if f["name"] == "payment_status")
    assert (field["label"], field["filterable"], field["sortable"], field["groupable"]) == ("Payment", True, True, True)
    view = next(v for v in meta["views"] if v["key"] == "to_settle")
    assert view["state"]["filters"] == [{"field": "payment_status", "op": "in", "value": [OVERDUE, DUE, NOT_SETTLED]}]
    await _exec(api, f"UPDATE sales_orders SET fulfillment_status = 'Cancelled' WHERE id = {o['9601']['id']}")
    assert (await _status(api, o["9601"]["id"]))["payment_status"] == NOT_NEEDED  # cancelled, never invoiced


async def test_a_finance_user_marks_settled_with_a_date_and_a_reference_and_undoes_it_with_a_reason(api):
    o = (await _orders(api, 9611))["9611"]
    before = await _status(api, o["id"])
    url = f"/api/v1/orders/{o['id']}/settle?version={before['version']}"
    body = {"settled_on": "2026-11-20", "reference": "EFT 55012", "amount": "112.50", "note": "Remittance 11-20"}
    # required: the date and the reference
    assert (await api.post(url, json={"reference": "EFT 55012"}, headers=FINANCE)).status_code == 422
    assert (await api.post(url, json={"settled_on": "2026-11-20", "reference": "  "}, headers=FINANCE)).status_code == 422
    r = await api.post(url, json={**body, "settled_on": (date.today() + timedelta(days=2)).isoformat()}, headers=FINANCE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "order.settled_future", r.text
    r = await api.post(url, json=body, headers=FINANCE)
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["payment_status"], s["settled_on"], s["settled_reference"], s["settled_amount"], s["settled_note"]) == (
        SETTLED, "2026-11-20", "EFT 55012", "112.50", "Remittance 11-20")
    assert s["settled_by_name"] and s["settled_at"]
    assert (s["order_state"], s["order_status"]) == (before["order_state"], before["order_status"])  # the state is untouched
    # a stale version and a second mark are refused
    assert (await api.post(url, json=body, headers=FINANCE)).status_code == 409
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version={s['version']}", json=body, headers=FINANCE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "order.settled"
    # Activity has the mark; undo needs a reason and is in Activity too
    undo = f"/api/v1/orders/{o['id']}/unsettle?version={s['version']}"
    assert (await api.post(undo, json={"reason": ""}, headers=FINANCE)).status_code == 422
    r = await api.post(undo, json={"reason": "wrong order, the EFT was for 9612"}, headers=FINANCE)
    assert r.status_code == 200 and r.json()["payment_status"] == NOT_SETTLED and r.json()["settled_on"] is None
    reasons = [t["reason"] for t in (await _status(api, o["id"]))["timeline"]]
    assert "settled on 2026-11-20, EFT 55012, 112.50: Remittance 11-20" in reasons
    assert "settlement undone (was 2026-11-20, EFT 55012): wrong order, the EFT was for 9612" in reasons
    r = await api.post(f"/api/v1/orders/{o['id']}/unsettle?version={r.json()['version']}", json={"reason": "again"}, headers=FINANCE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "order.not_settled"
    assert (await api.post(f"/api/v1/orders/{o['id']}/settle", json=body, headers=FINANCE)).json()["error"]["code"] == (
        "entity.version_required")  # the version check, as on other edits


# ── the record actions (platform/bulk_actions.py): one rule and one run, on one order or many ──────────────
ACT = "/api/v1/orders/actions/"
SETTLE_INPUT = {"settled_on": "2026-11-22", "reference": "REM 77"}


@pytest.mark.parametrize("action_id", sorted(ORDER_ACTIONS.actions))
async def test_every_order_action_refuses_without_its_permission_and_admits_with_it(api, action_id):
    """Generated from the registry: a new action gets both tests (the UI's hiding is not security)."""
    o = (await _orders(api, 9621))["9621"]
    perm = ORDER_ACTIONS[action_id].permission
    GRANTS["without"] = (set().union(*GRANTS.values()) | {"sales.file.retry", "sales.transfer.manage"}) - {perm}
    GRANTS["with"] = {"sales.read", perm}
    body = {"ids": {str(o["id"]): o["version"]}, "dry_run": True}
    r = await api.post(ACT + action_id, json=body, headers={"Authorization": f"Bearer {make_token('without')}"})
    assert r.status_code == 403, r.text
    r = await api.post(ACT + action_id, json=body, headers={"Authorization": f"Bearer {make_token('with')}"})
    assert r.status_code == 200 and r.json()["dry_run"] is True, r.text


async def test_only_finance_settles_an_operator_is_refused(api):
    o = (await _orders(api, 9621))["9621"]
    body = {"settled_on": "2026-11-20", "reference": "CHQ 1001"}
    for path, b in ((f"/api/v1/orders/{o['id']}/settle", body), (f"/api/v1/orders/{o['id']}/unsettle", {"reason": "test"}),
                    (ACT + "settle", {"ids": {str(o["id"]): o["version"]}, "input": body})):
        assert (await api.post(path, json=b, headers=OPERATOR)).status_code == 403, path  # operate, invoice, config: no
    assert (await _status(api, o["id"]))["payment_status"] == NOT_SETTLED
    r = await api.post(f"/api/v1/orders/{o['id']}/settle?version={o['version']}", json=body, headers=FINANCE)
    assert r.status_code == 200 and r.json()["payment_status"] == SETTLED  # admitted


async def test_mark_settled_on_many_one_result_each_its_own_activity_and_a_second_run_writes_nothing(api):
    o = await _orders(api, 9631, 9632, 9633, 9634)
    first = {"settled_on": "2026-11-21", "reference": "EFT 1"}
    assert (await api.post(f"/api/v1/orders/{o['9632']['id']}/settle?version={o['9632']['version']}", json=first,
                           headers=FINANCE)).status_code == 200
    await _exec(api, f"UPDATE sales_orders SET version = version + 1 WHERE id = {o['9634']['id']}")  # changed meanwhile
    ids = {str(o[n]["id"]): o[n]["version"] for n in ("9631", "9632", "9633", "9634")} | {"999999": 1}
    # the confirm step: a dry run, no input needed, nothing written
    r = (await api.post(ACT + "settle", json={"ids": ids, "dry_run": True}, headers=FINANCE)).json()
    assert r["counts"] == {"done": 2, "skipped": 1, "failed": 2} and r["dry_run"] is True
    assert (await _status(api, o["9631"]["id"]))["payment_status"] == NOT_SETTLED
    r = await api.post(ACT + "settle", json={"ids": ids, "input": SETTLE_INPUT}, headers=FINANCE)
    assert r.status_code == 200, r.text
    res = {x["id"]: x for x in r.json()["results"]}
    assert [res[o[n]["id"]]["status"] for n in ("9631", "9632", "9633", "9634")] + [res[999999]["status"]] == [
        "done", "skipped", "done", "failed", "failed"]
    assert "already settled on 2026-11-21" in res[o["9632"]["id"]]["reason"]  # the earlier mark stays
    assert res[o["9634"]["id"]]["reason"].startswith("changed since you selected it")
    rows = await api.env.q("SELECT source_order_number, settled_reference FROM sales_orders ORDER BY id")
    assert [(x["source_order_number"], x["settled_reference"]) for x in rows] == [
        ("9631", "REM 77"), ("9632", "EFT 1"), ("9633", "REM 77"), ("9634", None)]
    for n in ("9631", "9633"):  # each order's own Activity entry, with its date and reference
        assert "settled on 2026-11-22, REM 77" in [t["reason"] for t in (await _status(api, o[n]["id"]))["timeline"]]
    # run again with the versions seen before (a double click): skipped, never a second write and never "changed"
    r = (await api.post(ACT + "settle", json={"ids": {k: ids[k] for k in (str(o["9631"]["id"]), str(o["9633"]["id"]))},
                                              "input": SETTLE_INPUT}, headers=FINANCE)).json()
    assert r["counts"] == {"done": 0, "skipped": 2, "failed": 0}
    again = {str(o[n]["id"]): res[o[n]["id"]]["version"] for n in ("9631", "9633")}
    # the input is checked once, before any order
    r = await api.post(ACT + "settle", json={"ids": ids, "input": {"reference": "x"}}, headers=FINANCE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "request.invalid"
    # Undo settled on many: the reason is required; one not settled is skipped
    r = await api.post(ACT + "unsettle", json={"ids": again | {str(o["9634"]["id"]): o["9634"]["version"] + 1},
                                               "input": {"reason": "wrong remittance"}}, headers=FINANCE)
    assert [x["status"] for x in r.json()["results"]] == ["done", "done", "skipped"]


async def test_all_matching_the_list_with_the_count_the_person_saw(api):
    o = await _orders(api, 9651, 9652, 9653)
    query = "filter=payment_status:in:Not settled|Due|Overdue&filter=source_order_number:in:9651|9652"
    r = await api.post(ACT + "settle", json={"query": query, "expected": 3, "input": SETTLE_INPUT}, headers=FINANCE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "action.list_changed"  # 2 match now, not 3
    r = await api.post(ACT + "settle", json={"query": query, "expected": 2, "input": SETTLE_INPUT}, headers=FINANCE)
    assert sorted(x["id"] for x in r.json()["results"]) == sorted([o["9651"]["id"], o["9652"]["id"]])
    assert r.json()["counts"]["done"] == 2
    r = await api.post(ACT + "settle", json={"input": SETTLE_INPUT}, headers=FINANCE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "action.targets"


async def test_historical_and_cancelled_orders_need_no_payment_and_are_not_settled(api):
    o = await _orders(api, 9661, 9662)
    await _exec(api, f"UPDATE sales_orders SET historical = true WHERE id = {o['9661']['id']}")
    await _exec(api, f"UPDATE sales_orders SET fulfillment_status = 'Cancelled' WHERE id = {o['9662']['id']}")
    assert [(await _status(api, o[n]["id"]))["payment_status"] for n in ("9661", "9662")] == [NOT_NEEDED, NOT_NEEDED]
    view = next(v for v in (await api.get("/api/v1/orders/meta", headers=FINANCE)).json()["views"] if v["key"] == "to_settle")
    assert NOT_NEEDED not in view["state"]["filters"][0]["value"]  # so To settle leaves them out
    r = (await api.post(ACT + "settle", json={"ids": {str(o[n]["id"]): o[n]["version"] for n in ("9661", "9662")},
                                              "input": SETTLE_INPUT}, headers=FINANCE)).json()
    assert [x["status"] for x in r["results"]] == ["skipped", "skipped"] and "Not needed" in r["results"][0]["reason"]
    r = await api.post(f"/api/v1/orders/{o['9661']['id']}/settle?version={o['9661']['version']}", json=SETTLE_INPUT,
                       headers=FINANCE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "order.not_settleable"
    assert payment_status(draft=False, cancelled=False, settled=False, invoice_sent=False, invoice_date=None, terms=None,
                          today=TODAY, historical=True) == NOT_NEEDED


@pytest.mark.parametrize("terms", ["Net 30", "net30"])
async def test_the_sql_rule_reads_the_terms_as_the_python_one(api, terms):
    o = (await _orders(api, 9641))["9641"]
    await _invoice(api, o["id"], days_ago=30, terms=terms)
    assert (await _status(api, o["id"]))["payment_status"] == DUE  # due today, not yet overdue


async def test_a_failed_skipped_or_dry_run_record_leaves_no_event_behind(api):
    """Review 2026-11-23: a record's events go with its writes. An action whose run raises an event and then fails
    (or is skipped, or a dry run) must not have that event dispatched at commit; the done records' events stay."""
    from app.platform.bulk_actions import ActionIn, ActionRegistry, BulkAction, run_action
    from app.platform.errors import BusinessError
    from app.platform.events import StatusChanged
    from app.services.order_views import OrderRepository

    o = await _orders(api, 9671, 9672, 9673)
    fail_id, skip_id = o["9672"]["id"], o["9673"]["id"]

    async def check(uow, rec):  # noqa: ARG001
        return "skip me" if rec.id == skip_id else None

    async def run(uow, rec, data):  # noqa: ARG001
        uow.events.raise_(StatusChanged(actor=uow.actor, machine="test", entity="order", entity_id=rec.id, old="a", new="b"))
        rec.notes = "touched"
        if rec.id == fail_id:
            raise BusinessError("test.fail", "this one fails after raising its event")

    registry = ActionRegistry(OrderRepository, [BulkAction("touch", permission="sales.settle", check=check, run=run)])
    ids = {o[n]["id"]: o[n]["version"] for n in ("9671", "9672", "9673")}
    finance = SimpleNamespace(user_id=uuid.uuid4(), name="Fin", role="finance", is_system=False)
    async with api.env.factory(finance) as u:
        dry = await run_action(u, registry, "touch", ActionIn(ids=ids, dry_run=True))
        assert dry.counts == {"done": 2, "skipped": 1, "failed": 0} and len(u.events) == 0  # a dry run keeps nothing
        out = await run_action(u, registry, "touch", ActionIn(ids=ids))
        assert [r.status for r in out.results] == ["done", "failed", "skipped"]
        pending = u.events.drain()
        assert [e.entity_id for e in pending] == [o["9671"]["id"]]  # only the done record's event
        for e in pending:
            u.events.raise_(e)
        await u.commit()
    rows = {r["id"]: r["notes"] for r in await api.env.q("SELECT id, notes FROM sales_orders")}
    assert rows[o["9671"]["id"]] == "touched" and rows[fail_id] != "touched" and rows[skip_id] != "touched"


async def test_a_future_settled_date_is_refused_once_before_any_order(api):
    o = await _orders(api, 9681, 9682)
    ids = {str(o[n]["id"]): o[n]["version"] for n in ("9681", "9682")}
    future = (date.today() + timedelta(days=3)).isoformat()
    r = await api.post(ACT + "settle", json={"ids": ids, "input": {"settled_on": future, "reference": "EFT 9"}}, headers=FINANCE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "order.settled_future"  # one answer, not one per order
    assert [(await _status(api, o[n]["id"]))["payment_status"] for n in ("9681", "9682")] == [NOT_SETTLED, NOT_SETTLED]
