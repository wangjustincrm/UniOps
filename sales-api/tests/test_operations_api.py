"""P2 over HTTP: Connections (with the secret's presence, the connection test,
pinning and the login latch), the document register, runs with "Run now",
and the log — each permission denied and admitted. The app's own routers and
services; the unit-of-work factory and the runtime are bound to sales_test
and the fake SFTP server."""
import asyncio

import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.deps import get_uow_factory
from app.integrations.epms_tasks import shared_tasks_table
from app.main import app
from app.models.records import AuditRecord
from app.platform.audit import AuditTrail
from app.platform.uow import UnitOfWorkFactory
from app.runtime import build_runtime, get_runtime
from app.services.connections import ConnectionService
from tests.conftest import make_token
from tests.test_intake import Fake, x12

GRANTS = {
    "sales_operator": {"sales.read", "sales.config", "sales.file.retry"},
    "retrier": {"sales.read", "sales.file.retry"},
    "sales_reader": {"sales.read"},
    "requester": set(),
}


async def matrix(session, actor, key):  # noqa: ARG001
    return key in GRANTS.get(actor.role or "", set())


def as_(role):
    return {"Authorization": f"Bearer {make_token(role)}"}


OP, RETRY, READER, NOBODY = as_("sales_operator"), as_("retrier"), as_("sales_reader"), as_("requester")
FAKE_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOYyzHbjnpUgsd3f+LSfKEJkQUehyBzwkRpZ0hBihPDh"


@pytest_asyncio.fixture
async def api(db_engine, monkeypatch):
    async with db_engine.begin() as c:
        await c.run_sync(lambda sc: shared_tasks_table(sa.MetaData()).metadata.create_all(sc, checkfirst=True))
        await c.execute(sa.text(
            "TRUNCATE sales_log, sales_document_links, sales_documents, sales_runs, sales_connections, "
            "sales_audit, sales_settings RESTART IDENTITY CASCADE"))
        await c.execute(sa.text("DELETE FROM tasks"))
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=matrix, audit=AuditTrail(AuditRecord))
    fake = Fake()
    runtime = build_runtime(settings, factory, sftp=fake)
    runtime.intake.sftp = fake
    monkeypatch.setattr(ConnectionService, "environ", {"SALES_SECRET_TEST": "pw"})
    app.dependency_overrides[get_uow_factory] = lambda: factory
    app.dependency_overrides[get_runtime] = lambda: runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.fake, c.runtime = fake, runtime
        yield c
    await runtime.background.drain(timeout=5)
    app.dependency_overrides.clear()


def conn_body(**over):
    body = {"name": "radiant", "kind": "SFTP", "host": "sftp-test", "port": 22, "username": "radiant",
            "inbound_folder": "/in", "processed_folder": "/processed", "error_folder": "/error",
            "after_read": "LEAVE", "mode": "TEST", "secret_name": "SALES_SECRET_TEST"}
    return {**body, **over}


C = "/api/v1/config/connections"


async def make_conn(api, **over):
    r = await api.post(C, json=conn_body(**over), headers=OP)
    assert r.status_code == 201, r.text
    return r.json()


# ── connections ──────────────────────────────────────────────────────────────

async def test_connection_crud_rules_and_secret_presence(api):
    r = await api.post(C, json=conn_body(host=None), headers=OP)
    assert r.status_code == 422 and r.json()["error"]["code"] == "connection.field_required"
    assert r.json()["error"]["details"]["field"] == "host"
    r = await api.post(C, json=conn_body(after_read="MOVE", processed_folder=None), headers=OP)
    assert r.json()["error"]["details"]["field"] == "processed_folder"
    r = await api.post(C, json=conn_body(secret_name="lower-case"), headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "secret_name"
    assert (await api.post(C, json=conn_body(), headers=READER)).status_code == 403

    c = await make_conn(api)
    assert c["secret_present"] is True and "pw" not in str(c)  # presence only, never the value
    assert c["stable_seconds"] == 60 and c["host_key"] is None
    other = await make_conn(api, name="truecommerce", secret_name="SALES_SECRET_MISSING")
    assert other["secret_present"] is False
    r = await api.post(C, json=conn_body(), headers=OP)
    assert r.status_code == 409 and r.json()["error"]["code"] == "connection.name_taken"
    listing = (await api.get(C, headers=READER)).json()
    assert [x["name"] for x in listing["items"]] == ["radiant", "truecommerce"]


async def test_connection_test_pinning_and_audit(api):
    c = await make_conn(api)
    assert (await api.post(f"{C}/{c['id']}/test", headers=READER)).status_code == 403
    api.fake.files.update({"/in/a.945": b"x", "/processed/b": b"y"})
    before = dict(api.fake.files)

    r = (await api.post(f"{C}/{c['id']}/test", headers=OP)).json()
    assert r["host_key"] == {"line": FAKE_KEY, "fingerprint": "SHA256:fake", "pinned": False, "matches": False}
    assert r["folders"] == [] and "not pinned" in r["error"]  # no login to an unpinned host

    r = await api.put(f"{C}/{c['id']}?version={c['version']}", json={"host_key": FAKE_KEY}, headers=OP)
    assert r.status_code == 200 and r.json()["host_key"] == FAKE_KEY
    r = (await api.post(f"{C}/{c['id']}/test", headers=OP)).json()
    assert r["host_key"]["matches"] and r["error"] is None
    assert {f["folder"]: f["files"] for f in r["folders"]} == {"/in": 1, "/processed": 1, "/error": 0}
    assert api.fake.files == before  # the test changed nothing (FR-OB-74)

    trail = (await api.get(f"{C}/{c['id']}/audit", headers=READER)).json()
    assert [(e["reason"], e["after"]) for e in trail][0] == ("update", {"host_key": FAKE_KEY})


async def test_clearing_the_login_latch_needs_sales_config(api):
    c = await make_conn(api, host_key=FAKE_KEY)
    async with api.runtime.uow_factory(__import__("app.platform.actors", fromlist=["SYSTEM_ACTOR"]).SYSTEM_ACTOR) as u:
        await u.session.execute(sa.text("UPDATE sales_connections SET auth_failed_at = now(), "
                                        "auth_failed_reason = 'refused' WHERE id = :i"), {"i": c["id"]})
        await u.commit()
    assert (await api.post(f"{C}/{c['id']}/clear-auth-latch", headers=RETRY)).status_code == 403
    r = await api.post(f"{C}/{c['id']}/clear-auth-latch", headers=OP)
    assert r.status_code == 200 and r.json()["auth_failed_at"] is None
    trail = (await api.get(f"{C}/{c['id']}/audit", headers=OP)).json()
    assert trail[0]["reason"] == "clear auth latch"


# ── run now, runs, documents, log ────────────────────────────────────────────

async def run_now(api, headers=OP):
    r = await api.post("/api/v1/runs/run-now", headers=headers)
    if r.status_code == 202:
        await asyncio.gather(*list(api.runtime.background._tasks))
    return r


async def test_run_now_needs_sales_file_retry_and_records_the_run(api):
    await make_conn(api, host_key=FAKE_KEY)
    api.fake.files.update({"/in/a.945": x12("945", "000000001"), "/in/b.945": x12("945", "000000001")})
    assert (await api.post("/api/v1/runs/run-now", headers=READER)).status_code == 403
    r = await run_now(api, RETRY)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    run = (await api.get(f"/api/v1/runs/{run_id}", headers=READER)).json()
    assert (run["status"], run["trigger"]) == ("SUCCEEDED", "MANUAL")
    assert run["summary"]["connections"]["radiant"] | {} == {
        "listed": 2, "too_new": 0, "already_here": 0, "not_ours": 0, "stored": 1, "duplicates": 1, "failed": 0,
        "status": "ok"}
    assert (await api.get("/api/v1/runs", headers=READER)).json()["total"] == 1
    assert (await api.get("/api/v1/runs", headers=NOBODY)).status_code == 403


async def test_run_now_while_a_cycle_runs_is_409(api):
    await make_conn(api, host_key=FAKE_KEY)
    api.fake.gate = asyncio.Event()
    first = await api.post("/api/v1/runs/run-now", headers=OP)
    assert first.status_code == 202
    second = await api.post("/api/v1/runs/run-now", headers=OP)
    assert second.status_code == 409 and second.json()["error"]["code"] == "run.already_running"
    api.fake.gate.set()
    await asyncio.gather(*list(api.runtime.background._tasks))


async def test_documents_register_raw_bytes_and_log(api):
    await make_conn(api, host_key=FAKE_KEY)
    api.fake.files.update({"/in/a.945": x12("945", "000000001"), "/in/b.945": x12("945", "000000001"),
                           "/in/mine.940": x12("940", "000000002")})
    run_id = (await run_now(api)).json()["run_id"]

    docs = (await api.get("/api/v1/documents?sort=id", headers=READER)).json()
    assert docs["total"] == 3 and all("content" not in d for d in docs["items"])
    a, b, mine = docs["items"]
    # P5 reads 945s in the same run: this stub (no order key, no W12) cannot be applied
    assert (a["processing_status"], a["has_content"]) == ("Failed", True)
    assert (b["processing_status"], b["has_content"]) == ("Duplicate", False)
    assert (mine["processing_status"], mine["kind"]) == ("Skipped", "940_EDI")
    r = await api.get("/api/v1/documents?filter=processing_status:eq:Failed", headers=READER)
    assert [d["id"] for d in r.json()["items"]] == [a["id"]]

    r = await api.get(f"/api/v1/documents/{a['id']}/raw", headers=READER)
    assert r.status_code == 200 and r.content == x12("945", "000000001")
    assert 'filename="a.945"' in r.headers["content-disposition"]
    r = await api.get(f"/api/v1/documents/{b['id']}/raw", headers=READER)
    assert r.status_code == 404 and r.json()["error"]["details"]["original_id"] == a["id"]
    assert (await api.get(f"/api/v1/documents/{a['id']}/raw", headers=NOBODY)).status_code == 403

    log = (await api.get(f"/api/v1/log?filter=run_id:eq:{run_id}&sort=id", headers=READER)).json()
    messages = [e["message"] for e in log["items"]]
    assert messages[0].startswith("cycle started") and messages[-1] == "cycle finished"
    assert any("duplicates document" in m for m in messages) and any("not ours" in m for m in messages)
    r = await api.get("/api/v1/log?search=not%20ours", headers=READER)
    assert r.json()["total"] == 1
    assert (await api.get("/api/v1/log", headers=NOBODY)).status_code == 403


async def shopify_partner(api):
    r = await api.post("/api/v1/config/partners", headers=OP, json={
        "code": "SHOP", "name": "Shopify", "channel": "SHOPIFY", "settlement_mode": "PREPAID",
        "sending_mode": "AUTOMATIC", "delay_minutes": 180, "invoice_method": "NONE", "dc_routing_required": False})
    assert r.status_code == 201, r.text


SHOP = dict(kind="SHOPIFY", host="example.myshopify.com", port=443, username=None, inbound_folder=None,
            processed_folder=None, error_folder=None, secret_name="SALES_SECRET_SHOPIFY", api_version="2026-04",
            partner_code="SHOP")


async def test_a_shopify_connection_test_reads_shop_info_and_never_touches_sftp(api):
    await shopify_partner(api)
    c = (await api.post(C, json=conn_body(name="Shopify", **SHOP), headers=OP)).json()
    r = (await api.post(f"{C}/{c['id']}/test", headers=OP)).json()
    assert r["shop"]["name"] == "Test store" and r["error"] is None and r["host_key"] is None
    assert api.fake.calls == []  # no SSH against an HTTPS host
    assert [m for m, _ in api.runtime.container.shopify.calls] == ["shop"]


def test_errors_are_shown_as_short_readable_text():
    import asyncio
    import socket

    import asyncssh

    from app.integrations.sftp import SftpAuthFailed, SftpHostKeyMismatch, SftpRefused, readable
    assert readable(SftpRefused("host 'x' is not in SALES_SFTP_ALLOWED_HOSTS")) == "host 'x' is not in SALES_SFTP_ALLOWED_HOSTS"
    cases = {
        SftpAuthFailed("raw"): "refused the login",
        SftpHostKeyMismatch("raw"): "different host key",
        asyncio.TimeoutError(): "did not answer in time",
        TimeoutError(): "did not answer in time",
        ConnectionRefusedError(111, "raw"): "refused the connection",
        socket.gaierror(-2, "raw"): "could not be found",
        asyncssh.ProtocolError("Invalid protocol banner: HTTP/1.1 400 Bad Request…"): "did not answer as an SFTP server",
        ValueError("raw library text"): "The connection failed (ValueError).",
    }
    for err, expected in cases.items():
        text = readable(err)
        assert expected in text and "raw" not in text and "HTTP/1.1" not in text, (err, text)
