"""NC sync runs — model/migration + service + API tests."""
import os
import uuid
from datetime import datetime, timezone, timedelta

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app

from app.models.nc_sync import RUNNING, SUCCESS, NcSyncRun


async def test_nc_sync_run_roundtrip(db_session):
    run = NcSyncRun(id=uuid.uuid4(), mode="incremental", status=RUNNING,
                    started_by=uuid.uuid4(),
                    started_at=datetime.now(timezone.utc))
    db_session.add(run)
    await db_session.flush()
    got = (await db_session.execute(
        select(NcSyncRun).where(NcSyncRun.id == run.id))).scalar_one()
    assert got.status == RUNNING
    assert got.vouchers_inserted == 0          # server default
    assert got.watermark_to is None


def test_nc_configured_all_or_nothing(monkeypatch):
    from app.core.config import settings
    from app.services import nc_sync
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(settings, f, "x")
    assert nc_sync.nc_configured() is True
    monkeypatch.setattr(settings, "nc_password", None)
    assert nc_sync.nc_configured() is False


def _mini_extract():
    from app.services.nc_sync import NcExtract
    # voucher 1: line 1 both-sided w/ cc+ioitem aux; line 2 payable w/ supplier aux
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"ASS1": ("0104", "E01", "CRM004", "", ""),
             "ASS2": ("", "", "", "SUP01", "")},
        vouchers=[("NCPK1", "2026", "07", 12, "test voucher",
                   "2026-07-10 09:00:00", "2026-07-11 08:00:00")],
        details=[
            ("NCPK1", 1, "5101", 150, 50, 150, 50, "CADPK", 1, "expense", "ASS1"),
            ("NCPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "payable", "ASS2"),
        ],
        max_creationtime="2026-07-11 08:00:00",
    )


def test_transform_maps_dims_and_nets_sides():
    from decimal import Decimal
    from app.services.nc_sync import transform
    cc_id, dept_id, ba_id = object(), object(), object()
    sup_id = object()
    vouchers, lines, dims, unmapped = transform(
        _mini_extract(), uni_cc={"MOH-0106-E01": cc_id},
        uni_dept={"0104": dept_id}, uni_ba={"CRM004": ba_id},
        uni_sup={"SUP01": (sup_id, "ACME Supplies")}, uni_cust={},
        skip_pks=set())
    assert len(vouchers) == 1 and vouchers[0]["jv_number"] == "JV-202607-0012"
    assert vouchers[0]["nc_pk"] == "NCPK1"
    l1 = next(l for l in lines if l[2] == 1)
    assert (l1[5], l1[6]) == (Decimal("100"), Decimal("0"))   # netted to debit
    assert l1[11] is cc_id and l1[12] is dept_id
    assert l1[13] is ba_id            # promoted income/expense item column
    assert len(dims) == 1 and dims[0][2] == "income_expense_item" and dims[0][3] is ba_id
    assert unmapped == 0
    l2 = next(l for l in lines if l[2] == 2)
    assert l2[14] is sup_id and l2[15] == "ACME Supplies"
    assert l1[14] is None                     # no partner aux on line 1


def test_transform_skips_existing_and_counts_unmapped():
    from app.services.nc_sync import NcExtract, transform
    ex = _mini_extract()
    # skip the only voucher -> nothing out
    v, l, d, _ = transform(ex, {}, {}, {}, uni_sup={}, uni_cust={}, skip_pks={"NCPK1"})
    assert v == [] and l == [] and d == []
    # unknown cc code -> unmapped counted (line still produced, cc_id None)
    ex2 = NcExtract(ccy=ex.ccy, aux={"ASS1": ("", "ZZZ", "", "", "")},
                    vouchers=ex.vouchers, details=ex.details[:1],
                    max_creationtime=ex.max_creationtime)
    v2, l2, _, unmapped2 = transform(ex2, {}, {}, {}, uni_sup={}, uni_cust={}, skip_pks=set())
    assert len(l2) == 1 and l2[0][11] is None and unmapped2 == 1


# ── worker tests (psycopg2 direct, finance_test DB) ──────────────────────────

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _pg(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


async def test_start_run_incremental_inserts_and_sets_watermark(db_session):
    # db_session fixture has migrated finance_test; worker writes via its own psycopg2 conn
    from app.services import nc_sync
    # seed a budget_account so the worker can resolve CRM004 -> a real UUID
    ba_uuid = uuid.uuid4()
    _pg("insert into budget_accounts (id, code, name, is_active, created_at, updated_at) "
        "values (%s, 'CRM004', 'CRM004 Test', true, now(), now())", (ba_uuid,))
    run_id = nc_sync.start_run("incremental", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows = _pg("select status, vouchers_inserted, lines_inserted, dims_inserted, "
               "watermark_to from nc_sync_runs where id = %s", (run_id,))
    assert rows[0] == ("success", 1, 2, 1, "2026-07-11 08:00:00")
    assert _pg("select count(*) from journal_vouchers where nc_source_pk = 'NCPK1'")[0][0] == 1
    assert _pg("select count(*) from journal_voucher_lines "
               "where income_expense_item_id is not null")[0][0] == 1
    assert _pg("select partner_name from journal_voucher_lines "
               "where account_code = '2202'")[0][0] == "SUP01"
    # second incremental: same extract -> pk skipped, 0 inserted, watermark kept
    run2 = nc_sync.start_run("incremental", uuid.uuid4(),
                             fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows2 = _pg("select status, vouchers_inserted, watermark_from, watermark_to "
                "from nc_sync_runs where id = %s", (run2,))
    assert rows2[0] == ("success", 0, "2026-07-11 08:00:00", "2026-07-11 08:00:00")


async def test_full_clears_and_reloads(db_session):
    from app.services import nc_sync
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    run_id = nc_sync.start_run("full", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows = _pg("select status, vouchers_deleted, vouchers_inserted "
               "from nc_sync_runs where id = %s", (run_id,))
    assert rows[0] == ("success", 1, 1)
    assert _pg("select count(*) from journal_vouchers where nc_source_pk is not null")[0][0] == 1


async def test_concurrent_run_blocked_and_stale_recovered(db_session):
    from datetime import timedelta
    from app.services import nc_sync
    from app.services.nc_sync import SyncAlreadyRunning
    # plant a fresh 'running' row -> new run must be refused
    _pg("insert into nc_sync_runs (id, mode, status, started_at, created_at, updated_at) "
        "values (%s, 'incremental', 'running', now(), now(), now())", (uuid.uuid4(),))
    with pytest.raises(SyncAlreadyRunning):
        nc_sync.start_run("incremental", uuid.uuid4(),
                          fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    # make it stale (>30 min) -> auto-failed, new run proceeds
    _pg("update nc_sync_runs set started_at = now() - interval '31 minutes', "
        "updated_at = now() - interval '31 minutes' "
        "where status = 'running'")
    run_id = nc_sync.start_run("incremental", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    assert _pg("select status from nc_sync_runs where id = %s", (run_id,))[0][0] == "success"
    assert _pg("select count(*) from nc_sync_runs where status='failed' "
               "and error='abandoned'")[0][0] == 1


async def test_worker_failure_marks_failed_and_rolls_back(db_session):
    from app.services import nc_sync

    def boom(wm):
        raise RuntimeError("NC unreachable")

    run_id = nc_sync.start_run("incremental", uuid.uuid4(), fetch=boom, pg_dsn=_TEST_DSN)
    rows = _pg("select status, error from nc_sync_runs where id = %s", (run_id,))
    assert rows[0][0] == "failed" and "NC unreachable" in rows[0][1]
    assert _pg("select count(*) from journal_vouchers where nc_source_pk is not null")[0][0] == 0


# ── API endpoint tests ────────────────────────────────────────────────────────


def _token(role="system_admin", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)


def _h(role="system_admin"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _configure_nc(monkeypatch):
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(app_settings, f, "x")


async def test_status_shape_and_can_sync(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.get("/finance/v1/nc-sync/status", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["can_sync"] is True and body["configured"] is True
    assert body["current_run"] is None and body["last_run"] is None
    r2 = await client.get("/finance/v1/nc-sync/status", headers=_h(role="finance_manager"))
    assert r2.json()["can_sync"] is False


async def test_post_guards(client, monkeypatch):
    # not configured -> 503
    monkeypatch.setattr(app_settings, "nc_host", None)
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 503
    _configure_nc(monkeypatch)
    # non-admin -> 403
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"},
                          headers=_h(role="finance_manager"))
    assert r.status_code == 403
    # full without confirm -> 422
    r = await client.post("/finance/v1/nc-sync", json={"mode": "full"}, headers=_h())
    assert r.status_code == 422
    # bad mode -> 422 (pydantic Literal)
    r = await client.post("/finance/v1/nc-sync", json={"mode": "bananas"}, headers=_h())
    assert r.status_code == 422


async def test_post_triggers_run_and_status_reports_it(client, monkeypatch, db_session):
    from app.api.v1 import nc_sync as api_mod
    _configure_nc(monkeypatch)
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    monkeypatch.setattr(api_mod, "_fetch", lambda wm: _mini_extract())
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    # endpoint runs the worker synchronously in tests? No — it schedules a thread;
    # poll the DB (worker writes via psycopg2, visible outside the async session)
    import time
    for _ in range(50):
        rows = _pg("select status from nc_sync_runs where id = %s", (run_id,))
        if rows and rows[0][0] != "running":
            break
        time.sleep(0.2)
    assert rows[0][0] == "success"
    r2 = await client.get("/finance/v1/nc-sync/status", headers=_h())
    assert r2.json()["last_run"]["vouchers_inserted"] == 1


async def test_post_conflict_when_running(client, monkeypatch):
    _configure_nc(monkeypatch)
    from app.api.v1 import nc_sync as api_mod
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    _pg("insert into nc_sync_runs (id, mode, status, started_at, created_at, updated_at) "
        "values (%s, 'incremental', 'running', now(), now(), now())", (uuid.uuid4(),))
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 409


async def test_status_sweeps_stale_running_row(client, monkeypatch):
    _configure_nc(monkeypatch)
    rid = uuid.uuid4()
    _pg("insert into nc_sync_runs (id, mode, status, started_at, created_at, updated_at) "
        "values (%s, 'incremental', 'running', now() - interval '31 minutes', now(), "
        "now() - interval '31 minutes')", (rid,))
    r = await client.get("/finance/v1/nc-sync/status", headers=_h())
    assert r.status_code == 200
    assert r.json()["current_run"] is None
    assert _pg("select status, error from nc_sync_runs where id = %s", (rid,))[0] == ("failed", "abandoned")


def test_resolve_aux_type_pks_validates_constants():
    from app.services.nc_sync import (AUX_COSTCENTER, AUX_DEPT, AUX_IOITEM,
                                      resolve_aux_type_pks)
    items = [(AUX_DEPT, "部门"), (AUX_COSTCENTER, "成本中心"), (AUX_IOITEM, "收支项目"),
             ("SUPPK0000000000000001"[:20], "供应商档案"), ("CUSPK0000000000000001"[:20], "客户档案")]
    got = resolve_aux_type_pks(items)
    assert got["supplier"] and got["customer"]
    assert got["department"] == AUX_DEPT
    # constant mismatch -> hard error (typevalue-prefix == pk_accassitem assumption)
    bad = [("WRONGPK0000000000001"[:20], "部门"), (AUX_COSTCENTER, "成本中心"),
           (AUX_IOITEM, "收支项目")]
    with pytest.raises(RuntimeError):
        resolve_aux_type_pks(bad)
