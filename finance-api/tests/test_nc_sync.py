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


def _mini_extract(tallydate="2026-07-11 09:00:00", pk_system="GL"):
    from app.services.nc_sync import NcExtract, _tallied
    # voucher 1: line 1 both-sided w/ cc+ioitem aux; line 2 payable w/ supplier aux
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"ASS1": ("0104", "E01", "CRM004", "", "", ""),
             "ASS2": ("", "", "", "SUP01", "", "")},
        vouchers=[("NCPK1", "2026", "07", 12, "test voucher",
                   "2026-07-10 09:00:00", "2026-07-11 08:00:00", tallydate, pk_system)],
        details=[
            ("NCPK1", 1, "5101", 150, 50, 150, 50, "CADPK", 1, "expense", "ASS1"),
            ("NCPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "payable", "ASS2"),
        ],
        max_creationtime="2026-07-11 08:00:00",
        # Use the real _tallied() rather than a hand-rolled comparison — a
        # fixture that re-implements the padding check with clean values
        # can't catch the padding bug it's meant to guard against.
        tallied={"NCPK1"} if _tallied(tallydate) else set(),
    )


def test_transform_nets_only_both_positive_line():
    # §14.7: a genuinely both-POSITIVE line (dr=150, cr=50) is the one case we
    # still net (to satisfy ck_jv_lines_one_side, which forbids orig_debit>0 AND
    # orig_credit>0). Red/one-sided lines are kept signed — see
    # test_transform_keeps_red_entries_signed.
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
    assert (l1[5], l1[6]) == (Decimal("100"), Decimal("0"))   # both-positive -> netted
    assert (l1[7], l1[8]) == (Decimal("100"), Decimal("0"))   # local likewise
    assert l1[11] is cc_id and l1[12] is dept_id
    assert l1[13] is ba_id            # promoted income/expense item column
    assert len(dims) == 1 and dims[0][2] == "income_expense_item" and dims[0][3] is ba_id
    assert unmapped == 0
    l2 = next(l for l in lines if l[2] == 2)
    assert l2[14] is sup_id and l2[15] == "ACME Supplies"
    assert l1[14] is None                     # no partner aux on line 1


def test_transform_keeps_red_entries_signed():
    # §14.7: NC 红字 = negative amount in its ORIGINAL column. A red credit
    # (creditamount < 0) must stay a negative credit — NOT flip to a positive
    # debit — so gross 借/贷 发生额 match NC's signed-per-column sums. Net
    # (dr-cr) is unchanged, so closing balances are unaffected.
    from decimal import Decimal
    from app.services.nc_sync import NcExtract, _tallied, transform
    e = NcExtract(
        ccy={"CADPK": "CAD"}, aux={},
        vouchers=[("NCPK9", "2026", "06", 7, "red reversal",
                   "2026-06-10 09:00:00", "2026-06-11 08:00:00",
                   "2026-06-11 09:00:00", "GL")],
        details=[
            # a red credit: creditamount = -100 (localcredit = -100)
            ("NCPK9", 1, "660101", 0, -100, 0, -100, "CADPK", 1, "", None),
            # its balancing normal debit
            ("NCPK9", 2, "660101", 0, 100, 0, 100, "CADPK", 1, "", None),
        ],
        max_creationtime="2026-06-11 08:00:00", tallied={"NCPK9"})
    _, lines, _, _ = transform(e, {}, {}, {}, {}, {}, set())
    red = next(l for l in lines if l[2] == 1)
    # local_debit (l[7]) stays 0, local_credit (l[8]) stays the signed -100 —
    # NOT flipped to (debit=100, credit=0).
    assert (red[7], red[8]) == (Decimal("0"), Decimal("-100"))
    assert (red[5], red[6]) == (Decimal("0"), Decimal("-100"))   # orig too


def test_transform_skips_existing_and_counts_unmapped():
    from app.services.nc_sync import NcExtract, transform
    ex = _mini_extract()
    # skip the only voucher -> nothing out
    v, l, d, _ = transform(ex, {}, {}, {}, uni_sup={}, uni_cust={}, skip_pks={"NCPK1"})
    assert v == [] and l == [] and d == []
    # unknown cc code -> unmapped counted (line still produced, cc_id None)
    ex2 = NcExtract(ccy=ex.ccy, aux={"ASS1": ("", "ZZZ", "", "", "", "")},
                    vouchers=ex.vouchers, details=ex.details[:1],
                    max_creationtime=ex.max_creationtime, tallied=ex.tallied)
    v2, l2, _, unmapped2 = transform(ex2, {}, {}, {}, uni_sup={}, uni_cust={}, skip_pks=set())
    assert len(l2) == 1 and l2[0][11] is None and unmapped2 == 1


def test_tallied_handles_oracle_char_padding():
    """GL_VOUCHER.TALLYDATE is CHAR(19): Oracle space-pads it, so an empty one
    arrives as '~' + 18 spaces, not '~'. Comparing raw called every voucher
    tallied and made the whole draft/posted split a no-op — $1.73M of un-tallied
    entries stayed in the GL. SQL hides it (Oracle pads the literal too) and so do
    fixtures; only real Oracle shows it. Measured: 39,706 tallied of 39,977."""
    from app.services.nc_sync import _tallied
    assert _tallied("~" + " " * 18) is False       # what Oracle actually sends
    assert _tallied("~") is False
    assert _tallied(" " * 19) is False
    assert _tallied(None) is False
    assert _tallied("") is False
    assert _tallied("2026-07-15 10:30:00") is True


def test_max_creationtime_ignores_padded_sentinel():
    """max_ct (watermark_to) is the same CHAR(19) padding bug _tallied guards
    against, one line away: '~' + 18 spaces is truthy and sorts above every
    real timestamp, so a naive max() would poison the watermark forever and
    silently stop the incremental sync from importing anything ever again."""
    from app.services.nc_sync import _max_creationtime
    values = ["2026-07-10 09:00:00", "~" + " " * 18, "2026-07-15 10:30:00", None, ""]
    assert _max_creationtime(values) == "2026-07-15 10:30:00"
    assert _max_creationtime(["~" + " " * 18, "~", " " * 19, None, ""]) is None


def test_transform_marks_untallied_vouchers_draft():
    # NC's TALLYDATE empty = not yet posted to NC's ledger. status was hardcoded
    # "posted", which put $1.73M of un-tallied entries (incl. future periods) into
    # reports that filter on status == POSTED (spec §14.4).
    from app.services.nc_sync import transform
    e = _mini_extract()                      # its voucher has a tallydate
    vs, _, _, _ = transform(e, {}, {}, {}, {}, {}, set())
    assert vs[0]["status"] == "posted"
    e2 = _mini_extract(tallydate=None)       # not tallied in NC
    vs2, _, _, _ = transform(e2, {}, {}, {}, {}, {}, set())
    assert vs2[0]["status"] == "draft"


def test_transform_rejects_unknown_currency():
    # ccy.get(curr, "CAD") silently defaulted — same class as coa_import's
    # `return "asset"`. USD/CNY/EUR/GBP are real on this book (spec §14.3).
    from app.services.nc_sync import NcSyncError, transform
    e = _mini_extract()
    e.ccy = {}                               # currency pk resolves to nothing
    with pytest.raises(NcSyncError):
        transform(e, {}, {}, {}, {}, {}, set())


def test_cc_by_dept_covers_the_four_measured_codes():
    # 956 lines lost their cost centre to exactly these 4 dept codes
    # (404+274+163+115). 0106/0104 were already reachable via CC_BY_CODE —
    # the dept fallback simply missed them (spec §14.2).
    from app.services.nc_sync import CC_BY_DEPT
    assert CC_BY_DEPT["0106"] == "MOH-0106-E01"
    assert CC_BY_DEPT["0104"] == "MOH-0104-P01"
    assert CC_BY_DEPT["0102"] == "GA-0107"
    assert CC_BY_DEPT["0108"] == "RD-0109"


def test_transform_account_aware_for_predreal_and_stores_nc_cc():
    # 510101 is a leaf under 5101 (a predreal category); the map routes 5101/0104/E01.
    # A 6602 line with dept 0106 is intentionally NOT in the map -> unmapped exception.
    from app.services.nc_sync import NcExtract, make_category_of, transform
    category_of = make_category_of({"510101": "5101", "5101": None, "6602": None})
    cc_map_rows = [{"account_code": "5101", "dept_code": "0104", "nc_cc_code": "E01",
                    "uniops_cc_code": "MOH-0106-E01"}]
    cc_id = object()
    e = NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"A1": ("0104", "E01", "", "", "", ""),
             "A2": ("0106", "", "", "", "", "")},
        vouchers=[("P1", "2026", "07", 1, "x", "2026-07-10 09:00:00",
                   "2026-07-11 08:00:00", "2026-07-11 09:00:00", "GL")],
        details=[("P1", 1, "510101", 100, 0, 100, 0, "CADPK", 1, "", "A1"),
                 ("P1", 2, "6602", 50, 0, 50, 0, "CADPK", 1, "", "A2")],
        max_creationtime="2026-07-11 08:00:00", tallied={"P1"})
    _, lines, _, unmapped = transform(
        e, {"MOH-0106-E01": cc_id}, {}, {}, {}, {}, set(),
        cc_map_rows=cc_map_rows, category_of=category_of)
    l1 = next(l for l in lines if l[2] == 1)
    assert l1[11] is cc_id and l1[16] == "E01"   # account-aware hit + raw NC cc stored
    l2 = next(l for l in lines if l[2] == 2)
    assert l2[11] is None                          # 6602 + 0106 not in map -> exception
    assert unmapped == 1


def test_transform_non_predreal_keeps_dict_fallback():
    # a non-predreal account (2202 payable) still resolves via CC_BY_DEPT — the
    # 20k+ balance-sheet lines must not regress when the map is in play.
    from app.services.nc_sync import NcExtract, make_category_of, transform
    category_of = make_category_of({"2202": None})   # not a predreal category
    cc_id = object()
    e = NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"A1": ("0104", "", "", "", "", "")},     # dept 0104 -> CC_BY_DEPT
        vouchers=[("P2", "2026", "07", 1, "x", "2026-07-10 09:00:00",
                   "2026-07-11 08:00:00", "2026-07-11 09:00:00", "GL")],
        details=[("P2", 1, "2202", 0, 100, 0, 100, "CADPK", 1, "", "A1")],
        max_creationtime="2026-07-11 08:00:00", tallied={"P2"})
    _, lines, _, _ = transform(
        e, {"MOH-0104-P01": cc_id}, {}, {}, {}, {}, set(),
        cc_map_rows=[], category_of=category_of)   # CC_BY_DEPT["0104"] == "MOH-0104-P01"
    assert lines[0][11] is cc_id


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


async def test_sync_statuses_flips_draft_to_posted_when_tally_appears(db_session):
    """_sync_statuses must re-check every previously-imported voucher's tally
    fact independently of whether it's touched by this run's transform —
    an already-imported pk is skip_pks'd, so this is the ONLY path that can
    ever bring it from draft to posted after the fact (spec §14.4.1).

    _mini_extract ties `tallied` to the same `tallydate` that drives status,
    so DB state and tally fact always agree by construction in every other
    test — the UPDATE branch never runs. Here we vary `tallied` independently
    via dataclasses.replace to force disagreement."""
    from dataclasses import replace
    from app.services import nc_sync

    base = _mini_extract(tallydate=None)  # imports as draft; tallied=set()
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: base, pg_dsn=_TEST_DSN)
    assert _pg("select status from journal_vouchers "
               "where nc_source_pk = 'NCPK1'")[0][0] == "draft"

    # Second (incremental) run: NCPK1 is already imported so transform skips
    # it entirely — but NC's tally fact now says it's tallied.
    retallied = replace(base, tallied={"NCPK1"})
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: retallied, pg_dsn=_TEST_DSN)
    assert _pg("select status from journal_vouchers "
               "where nc_source_pk = 'NCPK1'")[0][0] == "posted"


async def test_sync_statuses_flips_posted_to_draft_when_tally_disappears(db_session):
    """Reverse direction of the above: stored posted, tally fact now says not
    tallied (e.g. NC un-tallied/voided it) -> must flip back to draft."""
    from dataclasses import replace
    from app.services import nc_sync

    base = _mini_extract()  # default tallydate -> imports as posted; tallied={"NCPK1"}
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: base, pg_dsn=_TEST_DSN)
    assert _pg("select status from journal_vouchers "
               "where nc_source_pk = 'NCPK1'")[0][0] == "posted"

    untallied = replace(base, tallied=set())
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: untallied, pg_dsn=_TEST_DSN)
    assert _pg("select status from journal_vouchers "
               "where nc_source_pk = 'NCPK1'")[0][0] == "draft"


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


def test_transform_maps_and_strips_subsystem():
    # PK_SYSTEM is CHAR(padded); store the stripped raw code, empty -> None
    from app.services.nc_sync import transform
    e = _mini_extract(pk_system="GL                  ")   # 空格补位
    vs, _, _, _ = transform(e, {}, {}, {}, {}, {}, set())
    assert vs[0]["source_subsystem"] == "GL"
    e2 = _mini_extract(pk_system="~")
    vs2, _, _, _ = transform(e2, {}, {}, {}, {}, {}, set())
    assert vs2[0]["source_subsystem"] is None


def test_resolve_aux_type_pks_validates_constants():
    from app.services.nc_sync import (AUX_COSTCENTER, AUX_DEPT, AUX_IOITEM,
                                      resolve_aux_type_pks)
    items = [(AUX_DEPT, "部门"), (AUX_COSTCENTER, "成本中心"), (AUX_IOITEM, "收支项目"),
             ("CLSPK000000000000001"[:20], "供应商基本分类"),   # must be ignored
             ("SUPPK000000000000001"[:20], "供应商档案"),
             ("SUPPK000000000000002"[:20], "供应商档案"),       # multi-org duplicate
             ("CUSPK000000000000001"[:20], "客户档案")]
    got = resolve_aux_type_pks(items)
    assert got["supplier"] == {"SUPPK000000000000001"[:20], "SUPPK000000000000002"[:20]}
    assert "CLSPK000000000000001"[:20] not in got["supplier"]
    assert got["customer"] == {"CUSPK000000000000001"[:20]}
    assert AUX_DEPT in got["department"]
    bad = [("WRONGPK0000000000001"[:20], "部门"), (AUX_COSTCENTER, "成本中心"),
           (AUX_IOITEM, "收支项目")]
    with pytest.raises(RuntimeError):
        resolve_aux_type_pks(bad)
