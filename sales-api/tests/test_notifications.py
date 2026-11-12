"""P8: notifications (FR-OB-56, AC-OB-49 / 52 / 71), the go-live gate,
Operations Health and the dashboard (FR-OB-52 / 52a / 52b, AC-OB-53) and where
a Task Inbox task opens. Real tasks table and users table (shared, epms-owned)
in sales_test; the email gateway is the guarded fake (D-b: switch off →
Suppressed with the "would have sent" row)."""
import uuid
from datetime import datetime, timezone
from itertools import product
from types import SimpleNamespace

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.container import build_container
from app.core.config import settings
from app.core.deps import get_uow_factory
from app.domain.notifications import BY_KEY, EVENT_KEYS, GATE_EVENTS
from app.domain.order_rules import order_state
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.email import FakeEmail
from app.integrations.epms_users import shared_users_table
from app.main import app
from app.models.config import Connection, NotificationRule
from app.models.orders import Order
from app.models.records import AuditRecord, OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.audit import AuditTrail
from app.platform.outbox import Outbox, OutboxWorker
from app.platform.uow import UnitOfWorkFactory
from app.runtime import build_runtime, get_runtime
from app.services import problems
from app.services.notifications import NOTIFY_EMAIL, email_kind
from app.services.order_views import ORDER_STATE_SQL
from app.services.problems import problem
from app.services.settings import OPERATOR_USER_ID, SCHEDULER_PAUSED, SETTINGS, SHADOW_MODE
from tests.conftest import make_token
from tests.test_operations_api import matrix
from tests.test_orders import T0, env  # noqa: F401 — env is a fixture

OPERATOR, ANN, BOB, GONE = (uuid.UUID(int=i) for i in (1, 2, 3, 4))


async def seed_users(engine):
    async with engine.begin() as c:
        await c.run_sync(lambda sc: shared_users_table(sa.MetaData()).metadata.create_all(sc, checkfirst=True))
        await c.execute(sa.text("DELETE FROM users WHERE email LIKE '%@users.example'"))
        for uid, name, active in ((OPERATOR, "Opal Operator", True), (ANN, "Ann Owner", True), (BOB, "Bob", True),
                                  (GONE, "Gone", False)):
            await c.execute(sa.text("INSERT INTO users (id, email, full_name, is_active) VALUES (:i, :e, :n, :a)"),
                            {"i": uid, "e": f"{name.split()[0].lower()}@users.example", "n": name, "a": active})
        await c.execute(sa.text("TRUNCATE sales_notification_rules, sales_notifications, sales_outbox RESTART IDENTITY"))


@pytest_asyncio.fixture
async def n(env, db_engine):  # noqa: F811
    await seed_users(db_engine)
    async with env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, OPERATOR_USER_ID, str(OPERATOR))
        await u.commit()
    mail, refused = FakeEmail(), []

    async def sink(r):
        refused.append(r)
    sw = SimpleNamespace(ENVIRONMENT="production", sftp_write_hosts=frozenset(), SALES_PARTNER_WRITES_ENABLED=False,
                         SALES_EXTERNAL_EMAIL_ENABLED=False)
    gw = build_container(sw, real_email=lambda: mail, refusal_sink=sink)
    worker = OutboxWorker(Outbox(OutboxRecord), {NOTIFY_EMAIL: email_kind(gw.email)}, env.factory)
    yield SimpleNamespace(env=env, mail=mail, refused=refused, switches=sw, worker=worker, q=env.q, f=env.factory)
    async with db_engine.begin() as c:  # leave nothing for the suites whose fixtures do not truncate it
        await c.execute(sa.text(
            "TRUNCATE sales_order_lines, sales_orders, sales_connections, sales_partners, sales_items, sales_settings, "
            "sales_notification_rules, sales_notifications, sales_outbox RESTART IDENTITY CASCADE"))
        await c.execute(sa.text("DELETE FROM tasks"))


async def rule(n, event, channel, users=(), emails=(), active=True):
    async with n.f(SYSTEM_ACTOR) as u:
        u.session.add(NotificationRule(event_type=event, channel=channel, recipients={
            "users": [str(x) for x in users], "emails": list(emails)}, active=active, version=1, created_by_name="t"))
        await u.commit()


async def raise_(n, event, entity, title="Something", text="What happened"):
    async with n.f(SYSTEM_ACTOR) as u:
        await n.env.tasks.raise_problem(u.session, event, entity, problem(event, title, text))
        await u.commit()


async def tasks(n, type_):
    return await n.q("SELECT assigned_user_id, title, is_completed FROM tasks WHERE type = :t", t=f"sales_{type_}")


async def emails(n):
    return await n.q("SELECT payload, status FROM sales_outbox WHERE kind = 'notify_email' ORDER BY id")


# ── the catalog ───────────────────────────────────────────────────────────────
def test_every_problem_the_module_raises_is_a_notification_event_type():
    raised = {v for k, v in vars(problems).items() if k.isupper() and isinstance(v, str)
              and k not in ("PREFIX", "DOCUMENT_TYPE", "ROLE")}
    assert raised and raised <= set(EVENT_KEYS)
    assert all(len(f"sales_{e}") <= 40 for e in EVENT_KEYS)  # the shared column's width
    assert set(GATE_EVENTS) == {"sftp_auth_failed", "shopify_auth_failed", "940_failed", "940_rejected", "940_timeout",
                                "cancel_after_940", "order_archived"}  # PRD section 11 step 3
    assert {e for e in EVENT_KEYS if BY_KEY[e].kind == "notify"} == {"945_validation", "partial_receipt"}


# ── routing ───────────────────────────────────────────────────────────────────
async def test_ac52_without_a_rule_the_task_goes_to_the_operator_and_no_email_is_sent(n):
    await raise_(n, "order_review", 7)
    [t] = await tasks(n, "order_review")
    assert t["assigned_user_id"] == OPERATOR
    assert await emails(n) == []
    [h] = await n.q("SELECT channel, problem_key, recipients FROM sales_notifications")
    assert (h["channel"], h["problem_key"], h["recipients"]) == ("TASK", "SALES:order_review:7", [str(OPERATOR)])


async def test_ac40_one_email_to_the_warehouse_and_the_owner_once_per_problem_and_suppressed_while_off(n):
    await rule(n, "cancel_after_940", "BOTH", users=[ANN, GONE], emails=["ops@radiant.example", "OPS@radiant.example"])
    await raise_(n, "cancel_after_940", 9, "Cancelled after the 940: order 109", "Nothing has shipped yet: <b>tell</b> them")
    await raise_(n, "cancel_after_940", 9, "Cancelled after the 940: order 109", "refreshed")  # the next cycle
    [t] = await tasks(n, "cancel_after_940")
    assert t["assigned_user_id"] == ANN  # the rule's first (active) user
    [e] = await emails(n)  # once per problem, never on the refresh
    assert e["payload"]["to"] == ["ann@users.example", "ops@radiant.example"]  # ONE email, everyone in To; GONE left out
    assert e["payload"]["subject"] == "Sales: Cancelled after the 940: order 109"
    assert "Nothing has shipped yet: <b>tell</b> them" in e["payload"]["body"]  # plain text: never rendered as HTML
    assert (await n.worker.run_once()).suppressed == 1  # D-b: the switch is off
    assert n.mail.sent == [] and n.refused[-1].context["recipients"] == ["ann@users.example", "ops@radiant.example"]
    assert [x["status"] for x in await emails(n)] == ["SUPPRESSED"]
    # switched on (production), a person sends it again: exactly one message
    n.switches.SALES_EXTERNAL_EMAIL_ENABLED = True
    async with n.f(SYSTEM_ACTOR) as u:
        await Outbox(OutboxRecord).requeue(u.session, (await n.q("SELECT id FROM sales_outbox"))[0]["id"])
        await u.commit()
    await n.worker.run_once()
    [m] = n.mail.sent
    assert m.to == ("ann@users.example", "ops@radiant.example") and m.body_html is None


async def test_a_notify_only_event_follows_its_channel_an_action_event_always_has_its_task(n):
    await raise_(n, "945_validation", 1)
    assert len(await tasks(n, "945_validation")) == 1  # no rule: a task, so it is seen (AC-OB-50)
    await rule(n, "945_validation", "EMAIL", emails=["qa@example.com"])
    await rule(n, "940_failed", "EMAIL", emails=["qa@example.com"])
    await raise_(n, "945_validation", 2)
    await raise_(n, "940_failed", 3)
    assert len(await tasks(n, "945_validation")) == 1  # EMAIL only: no second task
    assert len(await tasks(n, "940_failed")) == 1      # FR-OB-06: someone must act — the task stays
    assert [e["payload"]["to"] for e in await emails(n)] == [["qa@example.com"], ["qa@example.com"]]


async def test_an_inactive_rule_is_no_rule(n):
    await rule(n, "940_failed", "BOTH", users=[ANN], emails=["qa@example.com"], active=False)
    await raise_(n, "940_failed", 3)
    [t] = await tasks(n, "940_failed")
    assert t["assigned_user_id"] == OPERATOR and await emails(n) == []


async def test_in_the_shadow_run_order_work_notifies_nobody_and_intake_problems_still_go_out(n):
    async with n.f(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SHADOW_MODE, True)
        await u.commit()
    await rule(n, "order_review", "BOTH", users=[ANN], emails=["qa@example.com"])
    await rule(n, "sftp_auth_failed", "BOTH", users=[ANN], emails=["qa@example.com"])
    await raise_(n, "order_review", 1)
    await raise_(n, "sftp_auth_failed", 2)
    assert await tasks(n, "order_review") == [] and len(await tasks(n, "sftp_auth_failed")) == 1
    assert len(await emails(n)) == 1  # the intake problem's (suppressed under D-b when sent)


# ── the dashboard's order state: SQL and Python agree ────────────────────────
async def test_the_order_state_in_sql_is_order_rules_order_state(n):
    async with n.f(SYSTEM_ACTOR) as u:
        for i, (f, inv) in enumerate(product([s.value for s in F], [s.value for s in Inv])):
            u.session.add(Order(partner_code="SHOP", source_type="SHOPIFY", source_order_id=f"st{i}", fulfillment_status=f,
                                invoice_status=inv, first_received_at=T0, version=1))
        await u.commit()
    async with n.f(SYSTEM_ACTOR) as u:
        rows = (await u.session.execute(sa.select(Order.fulfillment_status, Order.invoice_status, ORDER_STATE_SQL)
                                        .where(Order.source_order_id.like("st%")))).all()
    assert len(rows) == len(F) * len(Inv)
    assert all(state == order_state(f, inv) for f, inv, state in rows)


# ── over HTTP: rules, test, history, gate, health, dashboard, inbox ──────────
def as_(role, sub=None):
    return {"Authorization": f"Bearer {make_token(role, sub)}"}


OP, READER = as_("sales_operator", OPERATOR), as_("sales_reader")
R = "/api/v1/config/notification-rules"


@pytest_asyncio.fixture
async def api(n, db_engine):
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=matrix, audit=AuditTrail(AuditRecord))
    runtime = build_runtime(settings, factory)
    app.dependency_overrides[get_uow_factory] = lambda: factory
    app.dependency_overrides[get_runtime] = lambda: runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.n = n
        yield c
    app.dependency_overrides.clear()


async def test_rules_are_checked_and_need_sales_config(api):
    ok = {"event_type": "cancel_after_940", "channel": "BOTH", "users": [str(ANN)], "emails": ["ops@radiant.example"]}
    assert (await api.post(R, json=ok, headers=READER)).status_code == 403
    for body, code in (({**ok, "event_type": "nope"}, 422), ({**ok, "emails": ["not-an-address"]}, 422),
                       ({**ok, "users": [str(GONE)]}, 422), ({**ok, "users": [], "emails": []}, 422)):
        r = await api.post(R, json=body, headers=OP)
        assert r.status_code == code, (body, r.text)
    r = await api.post(R, json=ok, headers=OP)
    assert r.status_code in (200, 201), r.text
    assert {k: r.json()[k] for k in ("event_label", "gate", "kind")} == {
        "event_label": "Cancelled at source after the 940", "gate": True, "kind": "action"}
    assert (await api.post(R, json=ok, headers=OP)).status_code in (409, 422)  # one rule per event
    events = (await api.get("/api/v1/config/notification-events", headers=READER)).json()
    assert {e["key"]: e["rule_id"] for e in events}["cancel_after_940"] == r.json()["id"]
    users = (await api.get("/api/v1/config/notification-users?q=ann", headers=READER)).json()
    assert [u["name"] for u in users] == ["Ann Owner"]


async def test_a_rule_test_writes_its_task_and_email_and_the_history_shows_both(api):
    r = (await api.post(R, json={"event_type": "940_failed", "channel": "BOTH", "users": [str(ANN)],
                                  "emails": ["qa@example.com"]}, headers=OP)).json()
    assert (await api.post(f"{R}/{r['id']}/test", headers=READER)).status_code == 403
    t = await api.post(f"{R}/{r['id']}/test", headers=OP)
    assert t.status_code == 200, t.text
    assert t.json()["rule"]["tested_at"] and "task written" in t.json()["result"] and "email queued" in t.json()["result"]
    [task] = await api.n.q("SELECT assigned_user_id, title FROM tasks WHERE type = 'sales_test'")
    assert task["assigned_user_id"] == ANN and task["title"] == "Test notification: 940 failed"
    h = (await api.get("/api/v1/notifications", headers=READER)).json()["items"]
    assert sorted((x["channel"], x["test"], x["delivery"]) for x in h) == [("EMAIL", True, "PENDING"), ("TASK", True, "Task written")]


async def test_ac71_a_channel_goes_live_only_through_the_notification_gate(api):
    async with api.n.f(SYSTEM_ACTOR) as u:
        c = Connection(name="radiant", kind="SFTP", host="sftp-test", port=22, username="r", after_read="LEAVE", mode="TEST",
                       inbound_folder="/in", processed_folder="/processed", error_folder="/error", outbound_folder="/out",
                       secret_name="SALES_SECRET_TEST", stable_seconds=0, version=1, created_by_name="t")
        u.session.add(c)
        await u.commit()
    live = await api.put(f"/api/v1/config/connections/{c.id}?version=1", json={"mode": "LIVE"}, headers=OP)
    assert live.status_code == 422 and live.json()["error"]["code"] == "connection.go_live_gate"
    assert set(live.json()["error"]["details"]["events"]) == set(GATE_EVENTS)
    for e in GATE_EVENTS:
        r = (await api.post(R, json={"event_type": e, "channel": "TASK", "users": [str(ANN)]}, headers=OP)).json()
        if e != "order_archived":
            await api.post(f"{R}/{r['id']}/test", headers=OP)
    gate = (await api.get("/api/v1/config/notification-gate", headers=READER)).json()
    assert [g["event_type"] for g in gate if not g["ok"]] == ["order_archived"]
    assert {g["problem"] for g in gate if not g["ok"]} == {"no test notification sent"}
    r = await api.put(f"/api/v1/config/connections/{c.id}?version=1", json={"mode": "LIVE"}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["events"] == ["order_archived"]
    rid = next(g["rule_id"] for g in gate if g["event_type"] == "order_archived")
    await api.post(f"{R}/{rid}/test", headers=OP)
    r = await api.put(f"/api/v1/config/connections/{c.id}?version=1", json={"mode": "LIVE"}, headers=OP)
    assert r.status_code == 200 and r.json()["mode"] == "LIVE"


async def test_ac53_health_automatic_runs_not_available_warns_and_a_latch_fails(api):
    h = (await api.get("/api/v1/operations/health", headers=READER)).json()
    by = {c["key"]: c for c in h["checks"]}
    assert by["settings"]["result"] == "FAILED" and "warehouse SFTP connection" in by["settings"]["detail"]
    assert by["scheduler"]["result"] == "WARNING" and "SALES_SCHEDULER_ENABLED" in by["scheduler"]["detail"]
    assert h["result"] == "FAILED" and h["cards"]["critical"] >= 1
    assert (h["shadow_mode"], h["external_email_enabled"], h["partner_writes_enabled"]) == (False, False, False)
    assert h["gate_ok"] is False and len(h["gate"]) == len(GATE_EVENTS)
    async with api.n.f(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, True)
        u.session.add(Connection(name="shop", kind="SFTP", host="h", port=22, username="u", after_read="LEAVE", mode="TEST",
                                 inbound_folder="/in", stable_seconds=0, version=1, created_by_name="t",
                                 auth_failed_at=datetime.now(timezone.utc), auth_failed_reason="Permission denied"))
        await u.commit()
    by = {c["key"]: c for c in (await api.get("/api/v1/operations/health", headers=READER)).json()["checks"]}
    assert by["scheduler"]["result"] == "WARNING" and "not available on this server" in by["scheduler"]["detail"]
    latch = next(c for k, c in by.items() if k.startswith("connection:"))
    assert latch["result"] == "FAILED" and "Permission denied" in latch["detail"]
    assert (await api.get("/api/v1/operations/health", headers=as_("requester"))).status_code == 403


async def test_every_dashboard_card_is_the_total_of_the_list_it_opens(api):
    async with api.n.f(SYSTEM_ACTOR) as u:
        for i, (f, inv, src, hist) in enumerate((
                (F.PENDING, Inv.PENDING, "SHOPIFY", False), (F.REVIEW, Inv.PENDING, "EDI_850", False),
                (F.SHIPPED, Inv.SENT, "SHOPIFY", False), (F.SENT_940, Inv.PENDING, "MANUAL", False),
                (F.SHIPPED, Inv.READY, "EDI_850", False), (F.SHIPPED, Inv.NOT_REQUIRED, "SHOPIFY", True))):
            u.session.add(Order(partner_code="SHOP", source_type=src, source_order_id=f"d{i}", fulfillment_status=f.value,
                                invoice_status=inv.value, historical=hist, first_received_at=T0, version=1))
        await u.commit()
    d = (await api.get("/api/v1/operations/dashboard", headers=READER)).json()
    cards = {c["key"]: c for c in d["cards"]}
    assert {k: c["count"] for k, c in cards.items() if k != "failed_files"} == {
        "active": 3, "on_hold": 1, "completed": 2, "cancelled": 0, "open_retailer": 2, "open_shopify": 1, "open_manual": 1,
        "warehouse_waiting": 2, "invoice_work": 1}  # the historical order counts like any other (user 2026-11-12)
    for c in d["cards"]:
        if c["key"] == "failed_files":
            continue
        params = [("filter", f"{f['field']}:{f['op']}:{'|'.join(f['value']) if isinstance(f['value'], list) else f['value']}")
                  for f in c["link"]["filters"]] + [("page_size", "1")]
        page = (await api.get("/api/v1/orders", params=params, headers=READER)).json()
        assert page["total"] == c["count"], c["key"]
    assert d["needs_review"] == 1


async def test_a_task_opens_its_record_in_sales(api):
    async def go(key):
        return (await api.get(f"/api/v1/inbox/{key}", headers=READER)).json()
    # ... and the tab of its sheet the task is about
    assert await go("SALES:order_review:23") == {"page": "/orders", "resource": "/orders", "id": 23, "tab": "overview"}
    assert (await go("SALES:short_shipment:23"))["tab"] == "shipments"
    assert (await go("SALES:940_rejected:23"))["tab"] == "documents"
    assert (await go("SALES:invoice_hold:23"))["tab"] == "invoice"
    assert await go("SALES:sftp_auth_failed:2") == {"page": "/config/connections", "resource": "/config/connections", "id": 2, "tab": "overview"}
    assert await go("SALES:partial_receipt:5") == {"page": "/transfers", "resource": "/transfers", "id": 5, "tab": "receipts"}
    assert await go("SALES:test:3") == {"page": "/config/notifications", "resource": "/config/notification-rules", "id": 3, "tab": None}
    assert await go("SALES:somethingnew:3") == {"page": "/home", "resource": None, "id": None, "tab": None}
    assert (await api.get("/api/v1/inbox/VMS:x:1", headers=READER)).status_code == 422


# ── the registrations outside sales-api (epms, Portal, epms-api; §14.2) ──────
def test_every_sales_task_type_is_registered_where_the_task_inbox_reads_it():
    import pathlib
    import re

    import pytest
    root = pathlib.Path(__file__).resolve().parents[2]
    files = {name: root / rel for name, rel in (("epms labels", "epms/src/lib/taskTypes.ts"),
                                                ("portal labels", "portal/src/pages/PortalHome.tsx"),
                                                ("epms-api liveness", "epms-api/app/crud/task.py"))}
    if not all(f.exists() for f in files.values()):
        pytest.skip("the monorepo's other services are not mounted here")
    types = {f"sales_{e}" for e in EVENT_KEYS} | {"sales_test"}
    for name, f in files.items():
        text = f.read_text(encoding="utf-8")
        found = set(re.findall(r"\b(sales_[a-z0-9_]+)\b", text))
        assert types <= found, (name, sorted(types - found))
    epms = files["epms labels"].read_text(encoding="utf-8")
    assert "if (docType === 'sales')" in epms and "salesHandoffHref(`/inbox/" in epms
    portal = files["portal labels"].read_text(encoding="utf-8")
    assert "t.document_type === 'sales'" in portal and "`/inbox/${encodeURIComponent(t.document_number)}`" in portal
