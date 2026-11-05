"""P2 intake and scheduler against the acceptance criteria: AC-OB-01…09 (file
intake, FR-OB-70..76) and AC-OB-46…48 (scheduler, FR-OB-07 / -50). The SFTP
server is the in-memory fake here (tests/integrations/test_sftp_real.py runs
the real gateway against a throwaway SFTP container); everything else is the
app's own code on the migrated sales_test schema."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.intake import classify
from app.integrations import sftp as sftp_mod
from app.integrations.epms_tasks import shared_tasks_table
from app.integrations.sftp import FakeSftp, SftpAuthFailed
from app.models.config import Connection
from app.models.documents import Document, Run
from app.models.records import AuditRecord, OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.audit import AuditTrail
from app.platform.guard import GuardedGateway
from app.platform.outbox import Outbox, OutboxWorker

from app.platform.uow import UnitOfWorkFactory
from app.services.cycle import SalesScheduler
from app.services.intake import ConnectionSnapshot, Intake
from app.services.problems import task_service

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


# ── sample files ─────────────────────────────────────────────────────────────

def x12(code: str, control: str = "000000123") -> bytes:
    def fix(v: str, n: int) -> str:
        return v.ljust(n)[:n]
    isa = "*".join(["ISA", "00", fix("", 10), "00", fix("", 10), "ZZ", fix("RADIANT", 15), "ZZ", fix("CRM", 15),
                    "261029", "1200", "U", "00401", control, "0", "P", ">"])
    assert len(isa) == 105
    body = f"GS*SW*RADIANT*CRM*20261029*1200*456*X*004010~ST*{code}*0001~W06*N*PO1~SE*3*0001~GE*1*456~IEA*1*{control}~"
    return (isa + "~" + body).encode()


PO_OK = '{"PurchaseOrder":[{"TradingPartner":"WMT","ShipTo":{"City":"Montréal"}}]}'.encode()
PO_BROKEN = b'{"PurchaseOrder":[{"Note1":"line one'  # cut off: not JSON (AC-OB-01)
PO_CONTROL_CHARS = b'{"PurchaseOrder":[{"Note1":"line one\r\nline two"}]}'  # raw CR/LF in a string: every real Walmart 850


class Fake(FakeSftp):
    """The fake, plus failures for one connection only (AC-OB-47)."""

    def __init__(self, files=None):
        super().__init__(files)
        self.fail_for: dict[tuple[int, str], BaseException] = {}
        self.gate: asyncio.Event | None = None

    async def list(self, conn, folder):
        if self.gate is not None:
            await self.gate.wait()
        if (conn.id, "list") in self.fail_for:
            self.calls.append(("list", (folder,)))
            raise self.fail_for[(conn.id, "list")]
        return await super().list(conn, folder)

    def count(self, method):
        return sum(1 for m, _ in self.calls if m == method)


async def fake_matrix(session, actor, key):  # noqa: ARG001
    return actor.role == "sales_operator"


@pytest_asyncio.fixture
async def env(db_engine):
    async with db_engine.begin() as c:
        await c.run_sync(lambda sc: shared_tasks_table(sa.MetaData()).metadata.create_all(sc, checkfirst=True))
        await c.execute(sa.text(
            "TRUNCATE sales_log, sales_document_links, sales_documents, sales_runs, sales_connections, "
            "sales_audit, sales_settings, sales_outbox RESTART IDENTITY CASCADE"))
        await c.execute(sa.text("DELETE FROM tasks"))
    factory = UnitOfWorkFactory(async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
                                permissions=fake_matrix, audit=AuditTrail(AuditRecord))
    fake = Fake()
    tasks = task_service(lambda: False)
    clock = SimpleNamespace(now=NOW)
    intake = Intake(uow_factory=factory, sftp=fake, tasks=tasks, clock=lambda: clock.now)

    async def add_conn(name="radiant", folder="/in", **kw):
        values = dict(name=name, kind="SFTP", host="sftp-test", username="u", secret_name="S",
                      inbound_folder=folder, processed_folder="/processed", error_folder="/error",
                      after_read="LEAVE", stable_seconds=60, mode="TEST", host_key="ssh-ed25519 AAAA", version=1)
        values.update(kw)
        async with factory(SYSTEM_ACTOR) as uow:
            c = Connection(**values)
            uow.session.add(c)
            await uow.session.flush()
            await uow.commit()
        return await snap(c.id)

    async def snap(conn_id):
        async with factory(SYSTEM_ACTOR) as uow:
            return ConnectionSnapshot.of(await uow.session.get(Connection, conn_id))

    async def q(sql, **p):
        async with db_engine.connect() as c:
            return (await c.execute(sa.text(sql), p)).mappings().all()

    return SimpleNamespace(factory=factory, fake=fake, tasks=tasks, intake=intake, clock=clock,
                           add_conn=add_conn, snap=snap, q=q, engine=db_engine)


def counts(r):
    return {k: v for k, v in r.as_dict().items() if v and k != "status"}


# ── AC-OB-01 / 02: store before parsing, tolerant UTF-8 ─────────────────────

async def test_ac01_unreadable_850_is_stored_as_is_and_raises_a_task(env):
    c = await env.add_conn(name="truecommerce")
    env.fake.files["/in/po-note.json"] = PO_BROKEN
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 1, "stored": 1} and r.balanced
    [doc] = await env.q("SELECT content, kind, processing_status, error FROM sales_documents")
    assert bytes(doc["content"]) == PO_BROKEN  # the original bytes
    assert (doc["kind"], doc["processing_status"]) == ("OTHER", "Pending")
    assert doc["error"].startswith("not valid JSON")
    [task] = await env.q("SELECT type, document_type, document_number, notified_at FROM tasks")
    assert (task["type"], task["document_type"]) == ("sales_unreadable_file", "sales")
    assert task["document_number"].startswith("SALES:unreadable_file:")
    assert task["notified_at"] is not None  # external email off: written as already notified (§3.5)


def test_raw_control_characters_inside_a_string_still_make_an_850():
    assert classify(PO_CONTROL_CHARS).kind == "850_JSON"  # strict JSON refused all 67 archived Walmart 850s


async def test_ac02_accented_address_stores_and_rereads_intact(env):
    c = await env.add_conn(name="truecommerce")
    env.fake.files["/in/po.json"] = PO_OK
    await env.intake.run_pass(c)
    [doc] = await env.q("SELECT content, kind, system FROM sales_documents")
    assert bytes(doc["content"]) == PO_OK and "Montréal" in bytes(doc["content"]).decode("utf-8")
    assert (doc["kind"], doc["system"]) == ("850_JSON", "TRUECOMMERCE")
    assert await env.q("SELECT 1 FROM tasks") == []  # readable: no task (the positive half of AC-OB-01)


# ── AC-OB-03 / 04: two identities ───────────────────────────────────────────

async def test_ac03_identical_bytes_under_two_names_are_stored_once(env):
    c = await env.add_conn()
    env.fake.files["/in/a.945"] = x12("945")
    env.fake.files["/in/b.945"] = x12("945")
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 2, "stored": 1, "duplicates": 1} and r.balanced
    rows = await env.q("SELECT id, remote_name, processing_status, content IS NULL AS empty, error "
                       "FROM sales_documents ORDER BY id")
    assert [(x["remote_name"], x["processing_status"], x["empty"]) for x in rows] == [
        ("a.945", "Pending", False), ("b.945", "Duplicate", True)]
    assert rows[1]["error"] == f"same bytes as document {rows[0]['id']}"


async def test_ac04_a_second_pass_over_an_unchanged_folder_downloads_nothing(env):
    c = await env.add_conn()
    env.fake.files.update({"/in/a.945": x12("945"), "/in/b.945": x12("945"), "/in/mine.940": x12("940"),
                           "/in/po.json": PO_OK})
    await env.intake.run_pass(c)
    gets = env.fake.count("get")
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 4, "already_here": 4} and r.balanced
    assert env.fake.count("get") == gets  # nothing downloaded again: stored, duplicate and not-ours alike


# ── AC-OB-05: after-read failure ────────────────────────────────────────────

async def test_ac05_a_failed_after_read_keeps_the_document_and_is_not_reapplied(env):
    c = await env.add_conn(after_read="MOVE")
    env.fake.files["/in/a.945"] = x12("945")
    env.fake.fail["move"] = OSError("permission denied on /processed")
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 1, "stored": 1}
    assert len(await env.q("SELECT 1 FROM sales_documents")) == 1
    [warn] = await env.q("SELECT message FROM sales_log WHERE level = 'WARNING'")
    assert "move of a.945 failed" in warn["message"] and "stays stored" in warn["message"]
    r = await env.intake.run_pass(c)  # still on the server: recognised, never stored or applied again
    assert counts(r) == {"listed": 1, "already_here": 1}
    assert len(await env.q("SELECT 1 FROM sales_documents")) == 1


async def test_after_read_move_and_delete_when_allowed(env):
    moved = await env.add_conn(name="m", folder="/in-m", after_read="MOVE")
    deleted = await env.add_conn(name="d", folder="/in-d", after_read="DELETE")
    env.fake.files.update({"/in-m/a.945": x12("945", "000000001"), "/in-d/b.945": x12("945", "000000002")})
    await env.intake.run_pass(moved)
    await env.intake.run_pass(deleted)
    assert sorted(env.fake.files) == ["/processed/a.945"]


# ── AC-OB-06: files still arriving ──────────────────────────────────────────

async def test_ac06_a_file_younger_than_60_seconds_is_too_new_and_read_later(env):
    c = await env.add_conn()
    env.fake.files["/in/a.945"] = x12("945")
    env.fake.mtimes["/in/a.945"] = NOW - timedelta(seconds=30)
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 1, "too_new": 1} and env.fake.count("get") == 0
    env.clock.now = NOW + timedelta(seconds=31)
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 1, "stored": 1}


# ── AC-OB-07: error folder; Leave changes nothing else; the test changes nothing

@pytest.mark.parametrize("policy", ["LEAVE", "MOVE", "DELETE"])
async def test_ac07_a_file_that_cannot_be_stored_goes_to_the_error_folder(env, policy):
    c = await env.add_conn(after_read=policy)
    env.fake.files.update({"/in/bad.945": x12("945", "000000001"), "/in/good.945": x12("945", "000000002")})
    real_get = env.fake.get

    async def get(conn, path):
        if path.endswith("bad.945"):
            raise OSError("read error")
        return await real_get(conn, path)
    env.fake.get = get
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 2, "stored": 1, "failed": 1} and r.balanced
    expected = {"LEAVE": ["/error/bad.945", "/in/good.945"], "MOVE": ["/error/bad.945", "/processed/good.945"],
                "DELETE": ["/error/bad.945"]}[policy]
    assert sorted(env.fake.files) == expected  # under Leave nothing else changed
    [task] = await env.q("SELECT type FROM tasks")
    assert task["type"] == "sales_file_not_stored"


async def test_ac07_the_connection_test_changes_nothing(env):
    from app.services.connections import ConnectionService
    c = await env.add_conn()
    env.fake.files.update({"/in/a.945": x12("945"), "/processed/old.945": b"x"})
    before = dict(env.fake.files)
    op = SimpleNamespace(user_id=uuid.uuid4(), name="Op", role="sales_operator", is_system=False)
    async with env.factory(op) as uow:
        result = await ConnectionService(uow).test(c.id, env.fake)
    assert env.fake.files == before
    assert {m for m, _ in env.fake.calls} == {"host_key"}  # the pin differs: no login, no listing
    assert result.host_key.pinned and not result.host_key.matches


# ── AC-OB-08: not ours ──────────────────────────────────────────────────────

async def test_ac08_our_own_940s_in_radiants_folder_are_not_ours(env):
    c = await env.add_conn(after_read="MOVE")
    env.fake.files.update({"/in/940-1.edi": x12("940", "000000001"), "/in/943-1.edi": x12("943", "000000002"),
                           "/in/810.json": b'{"Invoice":{}}', "/in/a.945": x12("945", "000000003")})
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 4, "not_ours": 3, "stored": 1} and r.balanced
    rows = await env.q("SELECT kind, processing_status, content IS NULL AS empty FROM sales_documents ORDER BY kind")
    assert [(x["kind"], x["processing_status"], x["empty"]) for x in rows] == [
        ("810_JSON", "Skipped", True), ("940_EDI", "Skipped", True), ("943_EDI", "Skipped", True),
        ("945_EDI", "Pending", False)]
    # Left where they are, even under Move: only the file we read moved.
    assert sorted(env.fake.files) == ["/in/810.json", "/in/940-1.edi", "/in/943-1.edi", "/processed/a.945"]


async def test_x12_control_numbers_are_kept(env):
    c = await env.add_conn()
    env.fake.files["/in/a.997"] = x12("997", "000000777")
    await env.intake.run_pass(c)
    [doc] = await env.q("SELECT kind, system, isa13, gs06, st02 FROM sales_documents")
    assert dict(doc) == {"kind": "997_EDI", "system": "RADIANT", "isa13": "000000777", "gs06": "456", "st02": "0001"}


# ── AC-OB-09: the pass report balances ──────────────────────────────────────

async def test_ac09_listed_equals_the_sum_of_the_other_six(env):
    c = await env.add_conn()
    env.fake.files.update({
        "/in/new.945": x12("945", "000000001"), "/in/a.945": x12("945", "000000002"),
        "/in/dup.945": x12("945", "000000002"), "/in/mine.940": x12("940", "000000003"),
        "/in/broken.json": PO_BROKEN,
    })
    env.fake.mtimes["/in/new.945"] = NOW
    seen = []
    for _ in range(2):
        r = await env.intake.run_pass(c)
        seen.append(counts(r))
        assert r.balanced, r
    assert seen[0] == {"listed": 5, "too_new": 1, "stored": 2, "duplicates": 1, "not_ours": 1}
    assert seen[1] == {"listed": 5, "too_new": 1, "already_here": 4}


# ── the guard on dev2: switch off → Suppressed → treated as Leave (§5.5) ────

@pytest.mark.parametrize("switch, mode, moved", [(False, "LIVE", False), (True, "TEST", False), (True, "LIVE", True)])
async def test_after_read_follows_the_switch_and_the_live_mode(env, switch, mode, moved):
    settings = SimpleNamespace(SALES_PARTNER_WRITES_ENABLED=switch)
    refused = []

    async def on_refused(r):
        refused.append(r)
    env.intake.sftp = GuardedGateway(env.fake, settings=settings, guarded=sftp_mod.guarded("SALES_PARTNER_WRITES_ENABLED"),
                                     reads=sftp_mod.READS, allow=sftp_mod.live_only, name="sftp",
                                     summarise=sftp_mod.summary, on_refused=on_refused)
    c = await env.add_conn(after_read="MOVE", mode=mode)
    env.fake.files["/in/a.945"] = x12("945")
    r = await env.intake.run_pass(c)
    assert counts(r) == {"listed": 1, "stored": 1}  # stored either way
    assert sorted(env.fake.files) == (["/processed/a.945"] if moved else ["/in/a.945"])
    assert len(refused) == (0 if moved else 1)
    if not moved:
        [info] = await env.q("SELECT message FROM sales_log WHERE message LIKE '%treated as Leave%'")
        assert "move of a.945 refused" in info["message"]


# ── scheduler: AC-OB-46 / 47 / 48 ───────────────────────────────────────────

def make_scheduler(env):
    return SalesScheduler(intake=env.intake, outbox_worker=OutboxWorker(Outbox(OutboxRecord), {}, env.factory),
                          run_model=Run, uow_factory=env.factory)


def make_cycle(env, conn_id):
    """A connection's job (single flight per connection, AC-OB-46)."""
    return make_scheduler(env).connection(conn_id)


async def test_ac46_two_cycles_cannot_run_at_once(env):
    c = await env.add_conn()
    cycle = make_cycle(env, c.id)
    env.fake.gate = asyncio.Event()
    first = asyncio.create_task(cycle.tick(trigger="MANUAL", force=True))
    for _ in range(100):
        if await env.q("SELECT 1 FROM sales_runs WHERE status = 'RUNNING'"):
            break
        await asyncio.sleep(0.02)
    assert await cycle.claim() is None  # "Run now" while one runs: refused
    env.fake.gate.set()
    run = await first
    assert run.status == "SUCCEEDED"
    assert await cycle.claim() is not None  # the positive half: free again


async def test_ac46_a_run_left_running_is_closed_as_failed_by_the_next_cycle(env):
    c = await env.add_conn()
    cycle = make_cycle(env, c.id)
    async with env.factory(SYSTEM_ACTOR) as uow:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        uow.session.add(Run(job=cycle.name, scope=cycle.scope, trigger="SCHEDULE", status="RUNNING", started_at=old,
                            heartbeat_at=old))
        await uow.commit()
    run = await cycle.tick(trigger="MANUAL", force=True)
    assert run is not None and run.status == "SUCCEEDED"
    rows = await env.q("SELECT status, error FROM sales_runs ORDER BY id")
    assert [r["status"] for r in rows] == ["FAILED", "SUCCEEDED"] and rows[0]["error"].startswith("stale")


async def automatic_runs_on(factory):
    """Automatic runs are Off until someone turns them on."""
    from app.services.settings import SCHEDULER_PAUSED, SETTINGS
    async with factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, False)
        await u.commit()


async def test_ac47_one_unreachable_partner_does_not_stop_the_others(env):
    bad = await env.add_conn(name="a-unreachable", folder="/in-a")
    await env.add_conn(name="b-fine", folder="/in-b")
    env.fake.fail_for[(bad.id, "list")] = OSError("connection timed out")
    env.fake.files["/in-b/a.945"] = x12("945")
    await automatic_runs_on(env.factory)
    await make_scheduler(env).tick()  # each connection its own job: never due yet, so both run
    runs = {r["scope"]: r for r in await env.q("SELECT scope, status, summary FROM sales_runs WHERE job = 'sales_connection'")}
    assert runs[f"connection:{bad.id}"]["status"] == "PARTIAL"
    assert runs[f"connection:{bad.id}"]["summary"]["pass"]["status"] == "list_failed"
    good = next(r for k, r in runs.items() if k != f"connection:{bad.id}")
    assert good["status"] == "SUCCEEDED" and good["summary"]["pass"]["stored"] == 1
    [task] = await env.q("SELECT type FROM tasks")
    assert task["type"] == "sales_sftp_fetch_failed"
    assert (await env.snap(bad.id)).auth_failed_at is None  # a network error is not latched


async def test_ac48_a_refused_login_is_reported_once_and_not_retried_until_cleared(env):
    from app.services.connections import ConnectionService
    c = await env.add_conn()
    env.fake.fail_for[(c.id, "list")] = SftpAuthFailed("login refused by sftp-test: bad password")
    cycle = make_cycle(env, c.id)
    await cycle.tick(trigger="MANUAL", force=True)
    await cycle.tick(trigger="MANUAL", force=True)
    assert env.fake.count("list") == 1  # the second cycle did not try again
    assert len(await env.q("SELECT 1 FROM tasks WHERE type = 'sales_sftp_auth_failed'")) == 1
    assert len(await env.q("SELECT 1 FROM sales_log WHERE message LIKE '%login refused%'")) == 1
    latched = await env.snap(c.id)
    assert latched.auth_failed_at is not None

    del env.fake.fail_for[(c.id, "list")]
    op = SimpleNamespace(user_id=uuid.uuid4(), name="Op", role="sales_operator", is_system=False)
    async with env.factory(op) as uow:
        await ConnectionService(uow).clear_latch(c.id, env.tasks)
        await uow.commit()
    [task] = await env.q("SELECT is_completed FROM tasks WHERE type = 'sales_sftp_auth_failed'")
    assert task["is_completed"] is True
    await cycle.tick(trigger="MANUAL", force=True)
    assert env.fake.count("list") == 2  # tried again once cleared


async def test_a_refused_login_mid_pass_latches_and_balances(env):
    c = await env.add_conn()
    env.fake.files.update({"/in/a.945": x12("945", "000000001"), "/in/b.945": x12("945", "000000002")})
    env.fake.fail["get"] = SftpAuthFailed("login refused")
    r = await env.intake.run_pass(c)
    assert r.status == "latched" and r.balanced and r.failed == 2
    assert (await env.snap(c.id)).auth_failed_at is not None


async def test_the_document_model_refuses_content_less_rows_other_than_identity_rows(env):
    from sqlalchemy.exc import IntegrityError
    async with env.factory(SYSTEM_ACTOR) as uow:
        uow.session.add(Document(direction="IN", origin="PARTNER", kind="945_EDI", sha256="0" * 64,
                                 processing_status="Pending", content=None))
        with pytest.raises(IntegrityError, match="ck_sales_documents_content"):
            await uow.session.flush()
