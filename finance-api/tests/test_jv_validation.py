"""JV dimension validation — the rules, the API, and the standing Admin Task.

Every rule gets BOTH a positive case (this line IS flagged) and the clean line
that must NOT be: a validator that flags everything and a validator that flags
nothing both pass a rule-fires-on-bad-data test, and only the pair separates
them.
"""
import os
import uuid
from datetime import date, datetime, timezone

import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.services import jv_validation as svc
from app.services import jv_validation_tasks as tasks

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _con():
    con = psycopg2.connect(_TEST_DSN)
    con.autocommit = True
    return con


def _exec(sql, params=()):
    con = _con(); cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


# ── seeding ──────────────────────────────────────────────────────────────────

CLEAN = "CLEAN"          # line_summary markers, so assertions can name a line
NO_CC = "NO_CC"          # map knows the dept, not the NC code
NO_MAP = "NO_MAP"        # map does not know this account x dept at all
UNRESOLVED_IO = "UNRESOLVED_IO"
NO_IO = "NO_IO"          # 6602 line with no income-expense dimension
FN_UNKNOWN = "FN_UNKNOWN" # 6603 account with no budget account of that code
FN_OK = "FN_OK"          # 6603 account that DOES have one
WRONG_CATEGORY = "WRONG_CATEGORY"
WRONG_DEPT = "WRONG_DEPT"
MAP_EXEMPT = "MAP_EXEMPT"
POLICY = "POLICY"        # payroll/depreciation/shut-down loss — excluded on purpose
NO_DEPT = "NO_DEPT"      # placed by the map via an NC department EPMS has no row for


def _seed():
    """A posted 2026-06 voucher whose lines each break exactly one thing, plus a
    clean line. Written over psycopg2 so the async session (separate connection)
    sees committed rows."""
    con = _con(); cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "budget_actual_cc_map", "cost_centers", "departments",
              "chart_of_accounts", "tasks"):
        cur.execute(f"delete from {t}")

    for code, parent in (("5101", None), ("510102", "5101"),
                         ("6601", None), ("660101", "6601"),
                         ("6602", None), ("6603", None),
                         ("660301", "6603"), ("660399", "6603")):
        cur.execute(
            "insert into chart_of_accounts (id, code, name, account_type, "
            "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
            "created_at, updated_at) values (%s,%s,%s,'expense','debit',true,%s,true,"
            "'[]'::jsonb, now(), now())", (uuid.uuid4(), code, code, parent))

    dept_moh, dept_ga, dept_sell = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for did, code in ((dept_moh, "0105"), (dept_ga, "0101"), (dept_sell, "0107")):
        cur.execute("insert into departments (id, code, name, is_active, created_at, "
                    "updated_at) values (%s,%s,%s,true,now(),now())", (did, code, code))

    cc_moh, cc_ga, cc_sell, cc_fn = (uuid.uuid4(), uuid.uuid4(),
                                     uuid.uuid4(), uuid.uuid4())
    for cid, code in ((cc_moh, "MOH-0105-LAB"), (cc_ga, "GA-0101"),
                      (cc_sell, "SELL-0107-S03"), (cc_fn, "FN-0103")):
        cur.execute("insert into cost_centers (id, code, name, is_active, created_at, "
                    "updated_at) values (%s,%s,%s,true,now(),now())", (cid, code, code))

    # The map as production has it: 5101 x 0105 demands the exact NC code 'Q01'.
    for acct, dept, nc_cc, uni in (("5101", "0105", "Q01", "MOH-0105-LAB"),
                                   ("6602", "0101", "ALL", "GA-0101"),
                                   ("6601", "0107", "ALL", "SELL-0107-S03"),
                                   # 6602 x dept 0107 deliberately parked in the
                                   # HR cost center — the exemption case.
                                   ("6602", "0107", "ALL", "GA-0101"),
                                   ("6603", "ALL", "ALL", "FN-0103")):
        cur.execute("insert into budget_actual_cc_map (id, account_code, dept_code, "
                    "nc_cc_code, uniops_cc_code, created_at, updated_at) "
                    "values (%s,%s,%s,%s,%s,now(),now())",
                    (uuid.uuid4(), acct, dept, nc_cc, uni))

    ba = uuid.uuid4()
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'CRM00201','Training',true,now(),now())", (ba,))
    # 6603 is budgeted BY ACCOUNT: 660301 has a budget account of the same code,
    # 660399 deliberately does not.
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'660301','Interest income',true,now(),now())",
                (uuid.uuid4(),))

    jv = uuid.uuid4()
    cur.execute(
        "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
        "fiscal_period, summary, status, total_debit, total_credit, "
        "total_local_debit, total_local_credit, created_at, updated_at) "
        "values (%s,'JV-202606-0001','JV','2026-06-15','2026-06','seed','posted',"
        "0,0,0,0,now(),now())", (jv,))

    def line(no, account, summary, *, cc=None, nc_cc=None, dept=None,
             io_id=None, io_text=None, debit="100.00"):
        lid = uuid.uuid4()
        cur.execute(
            "insert into journal_voucher_lines (id, jv_id, line_no, account_code, "
            "summary, orig_debit, orig_credit, local_debit, local_credit, currency, "
            "fx_rate, cost_center_id, nc_cc_code, department_id, "
            "income_expense_item_id, created_at, updated_at) "
            "values (%s,%s,%s,%s,%s,%s,0,%s,0,'CAD',1,%s,%s,%s,%s,now(),now())",
            (lid, jv, no, account, summary, debit, debit, cc, nc_cc, dept, io_id))
        if io_text is not None:
            cur.execute(
                "insert into jv_line_dimensions (id, jv_line_id, dim_code, value_id, "
                "value_text, created_at, updated_at) "
                "values (%s,%s,'income_expense_item',%s,%s,now(),now())",
                (uuid.uuid4(), lid, io_id, io_text))
        return lid

    # Clean: MOH account, MOH cost center, dept matches, resolved budget account.
    line(1, "510102", CLEAN, cc=cc_moh, nc_cc="Q01", dept=dept_moh,
         io_id=ba, io_text="CRM00201")
    # JV-202608-0408's shape: NC filled 'QA', the map knows only 'Q01'.
    line(2, "510102", NO_CC, nc_cc="QA", dept=dept_moh, io_id=ba, io_text="CRM00201")
    # 6602 x dept 0104's shape: the map has no row for this account x
    # department at all, so no amount of re-syncing places it — somebody has to
    # decide where it belongs first.
    line(8, "510102", NO_MAP, nc_cc="H01", dept=dept_ga, io_id=ba, io_text="CRM00201")
    # JV-202608-0079's shape: an income-expense code with no budget account.
    line(3, "6602", UNRESOLVED_IO, cc=cc_ga, nc_cc=None, dept=dept_ga,
         io_text="CRM671105", debit="27212.38")
    # No income-expense dimension at all, on an account family that is booked
    # by income-expense item — a real gap.
    line(4, "6602", NO_IO, cc=cc_ga, dept=dept_ga)
    # 6603 is booked by ACCOUNT, no income-expense item involved: the one with a
    # matching budget account is clean, the one without is the finding.
    line(11, "660301", FN_OK, cc=cc_fn, dept=dept_ga)
    line(12, "660399", FN_UNKNOWN, cc=cc_fn, dept=dept_ga)
    # A manufacturing account booked to a G&A cost center.
    line(5, "510102", WRONG_CATEGORY, cc=cc_ga, dept=dept_ga, io_id=ba, io_text="CRM00201")
    # Cost center GA-0101 (dept segment 0101) on a line stamped dept 0105.
    line(6, "6602", WRONG_DEPT, cc=cc_ga, dept=dept_moh, io_id=ba, io_text="CRM00201")
    # Same disagreement, but the map says so on purpose -> exempt.
    line(7, "6602", MAP_EXEMPT, cc=cc_ga, dept=dept_sell, io_id=ba, io_text="CRM00201")
    # Placed correctly by the sync off an NC department code that EPMS has no
    # departments row for (production: 0108 -> GA-0100). The mirror stores only
    # the resolved department_id, so it is NULL here and the drift rule cannot
    # replay the decision — it must stay quiet rather than call this drift.
    line(10, "6602", NO_DEPT, cc=cc_ga, dept=None, io_id=ba, io_text="CRM00201")
    # Shut-down loss: no budget account, deliberately outside the dashboard. It
    # must NOT be reported as an unresolved income-expense item.
    line(9, "6602", POLICY, cc=cc_ga, dept=dept_ga, io_text="CRM09912", debit="8621672.11")

    con.close()
    return {"jv_id": jv, "cc_moh": cc_moh, "cc_ga": cc_ga, "ba": ba,
            "dept_moh": dept_moh}


@pytest.fixture
def seeded(db_session):
    """db_session runs the migrations; this seeds over them."""
    return _seed()


async def _flagged(db_session, rule=None):
    """{line_summary} flagged by `rule` (or by anything)."""
    res = await svc.rows(db_session, "2026-01", "2026-12", rule=rule, limit=500)
    return {r["line_summary"] for r in res["rows"]}


# ── rules: each fires, and the clean line never does ─────────────────────────

async def test_cost_center_code_unmatched_fires(db_session, seeded):
    """The map lists 5101 x 0105 -> 'Q01'; the voucher says 'QA'."""
    hit = await _flagged(db_session, "cost_center_code_unmatched")
    assert NO_CC in hit
    assert CLEAN not in hit
    assert NO_MAP not in hit          # that one is the other half


async def test_cost_center_no_map_for_department_fires(db_session, seeded):
    """5101 x dept 0101 is in no map row — a decision, not a missing row."""
    hit = await _flagged(db_session, "cost_center_no_map_for_department")
    assert NO_MAP in hit
    assert NO_CC not in hit
    assert CLEAN not in hit


async def test_the_two_cost_center_rules_partition_the_unplaced_lines(db_session, seeded):
    """Together they must cover every line with no cost center, and never the
    same line twice — they replaced one rule and must still add up to it."""
    unmatched = await _flagged(db_session, "cost_center_code_unmatched")
    no_map = await _flagged(db_session, "cost_center_no_map_for_department")
    assert unmatched & no_map == set()
    assert unmatched | no_map == {NO_CC, NO_MAP}


async def test_income_expense_unresolved_fires(db_session, seeded):
    hit = await _flagged(db_session, "income_expense_unresolved")
    assert UNRESOLVED_IO in hit
    assert CLEAN not in hit and NO_IO not in hit    # NO_IO is the other rule
    assert POLICY not in hit                       # bypassed entirely


async def test_policy_exclusions_are_bypassed_entirely(db_session, seeded):
    """Payroll / depreciation / shut-down loss are not held to placement rules.

    They are kept out of the per-cost-centre dashboard by decision, so a line
    of theirs with no cost centre is not a defect — it is the arrangement. They
    used to be listed under their own rule; reporting 6,572 lines nobody can or
    should act on buried the 33 that matter (user, 2026-09-17).
    """
    assert POLICY not in await _flagged(db_session)


async def test_6603_is_checked_by_account_not_by_income_expense_item(db_session, seeded):
    """NC books financial expenses by ACCOUNT — 660301 Interest income,
    660303 Bank Charge — and never puts an income-expense item on them. Holding
    them to the income-expense rule reported all 684 of them as broken while
    every one was booked exactly as finance intends."""
    missing = await _flagged(db_session, "income_expense_missing")
    assert NO_IO in missing                      # 6602 with no item: a real gap
    assert FN_UNKNOWN not in missing             # 6603: wrong rule for it
    assert FN_OK not in missing

    not_in_catalog = await _flagged(db_session, "account_not_in_catalog")
    assert FN_UNKNOWN in not_in_catalog          # 660399 has no budget account
    assert FN_OK not in not_in_catalog           # 660301 has one
    assert NO_IO not in not_in_catalog


async def test_a_6603_line_with_its_budget_account_is_clean(db_session, seeded):
    assert FN_OK not in await _flagged(db_session)


async def test_income_expense_missing_fires(db_session, seeded):
    hit = await _flagged(db_session, "income_expense_missing")
    assert NO_IO in hit
    assert CLEAN not in hit and UNRESOLVED_IO not in hit


async def test_category_mismatch_fires(db_session, seeded):
    hit = await _flagged(db_session, "category_mismatch")
    assert WRONG_CATEGORY in hit                    # 510102 booked to GA-0101
    assert CLEAN not in hit


async def test_department_mismatch_fires_but_respects_the_map(db_session, seeded):
    hit = await _flagged(db_session, "department_mismatch")
    assert WRONG_DEPT in hit
    # The map places 6602 x dept 0107 in GA-0101 deliberately: not a finding.
    assert MAP_EXEMPT not in hit
    assert CLEAN not in hit


async def test_clean_line_breaks_no_rule_at_all(db_session, seeded):
    assert CLEAN not in await _flagged(db_session)


async def test_every_seeded_defect_is_found(db_session, seeded):
    """The suite would still pass if a rule silently stopped matching anything
    OTHER than its own fixture line; this pins the whole set."""
    hit = await _flagged(db_session)
    assert {NO_CC, NO_MAP, UNRESOLVED_IO, NO_IO, FN_UNKNOWN,
            WRONG_CATEGORY, WRONG_DEPT} <= hit


# ── cc_map_drift: the preview for a mapping change ───────────────────────────

async def test_cc_map_drift_is_quiet_until_the_map_changes(db_session, seeded):
    before = await _flagged(db_session, "cc_map_drift")
    assert NO_CC not in before          # nothing resolves it today either

    # Widen the map the way the SELL-0107-S03 fix does: accept any NC cost center.
    _exec("update budget_actual_cc_map set nc_cc_code='ALL' "
          "where account_code='5101' and dept_code='0105'")
    after = await _flagged(db_session, "cc_map_drift")
    assert NO_CC in after               # a re-sync would now place this line
    rows = await svc.rows(db_session, "2026-01", "2026-12", rule="cc_map_drift")
    row = next(r for r in rows["rows"] if r["line_summary"] == NO_CC)
    assert row["expected_cost_center_code"] == "MOH-0105-LAB"
    assert row["cost_center_code"] is None


async def test_drift_stays_quiet_on_lines_it_cannot_replay(db_session, seeded):
    """A line the sync placed via an NC department code EPMS has no row for.

    Production hit this the day the rule shipped: two 2025 lines sat in GA-0100
    off the map's 6602/0108 row, EPMS has no department 0108, and recomputing
    from the resulting NULL department "expected" no cost center — so a
    correctly placed line was reported as drift. The rule must be silent where
    it cannot replay the decision, not confidently wrong.
    """
    assert NO_DEPT not in await _flagged(db_session, "cc_map_drift")
    # And silent here does not mean silent everywhere: the line is clean.
    assert NO_DEPT not in await _flagged(db_session)


# ── summary + period window ──────────────────────────────────────────────────

async def test_summary_counts_and_amounts(db_session, seeded):
    s = {r["rule"]: r for r in await svc.summary(db_session, "2026-01", "2026-12")}
    assert [r for r in svc.RULES] == list(s)                  # stable rule order
    assert s["income_expense_unresolved"]["lines"] == 1
    assert s["income_expense_unresolved"]["debit"] == "27212.38"
    assert s["income_expense_unresolved"]["drops_from_dashboard"] is True
    assert s["category_mismatch"]["drops_from_dashboard"] is False
    assert s["account_not_in_catalog"]["drops_from_dashboard"] is True
    # Only the no-map half needs a human ruling before anything can be fixed.
    assert s["cost_center_no_map_for_department"]["needs_decision"] is True
    assert s["cost_center_code_unmatched"]["needs_decision"] is False


async def test_period_window_excludes_other_periods(db_session, seeded):
    s = {r["rule"]: r for r in await svc.summary(db_session, "2026-07", "2026-12")}
    assert all(r["lines"] == 0 for r in s.values())


async def test_draft_vouchers_are_not_validated(db_session, seeded):
    _exec("update journal_vouchers set status='draft'")
    assert await _flagged(db_session) == set()


# ── API ──────────────────────────────────────────────────────────────────────

def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
                      "exp": datetime.now(timezone.utc).timestamp() + 3600},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    app.dependency_overrides.clear()


async def test_api_summary_and_lines(client, seeded):
    async with client as c:
        r = await c.get("/finance/v1/gl/jv-validation",
                        params={"period_from": "2026-01", "period_to": "2026-12"},
                        headers=_h())
        assert r.status_code == 200
        rules = {x["rule"]: x for x in r.json()["rules"]}
        assert rules["cost_center_code_unmatched"]["lines"] == 1

        r = await c.get("/finance/v1/gl/jv-validation/lines",
                        params={"period_from": "2026-01", "period_to": "2026-12",
                                "rule": "cost_center_code_unmatched"}, headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["rows"][0]["nc_cost_center_code"] == "QA"


async def test_api_rejects_bad_input(client, seeded):
    async with client as c:
        for params in ({"period_from": "2026-1", "period_to": "2026-12"},
                       {"period_from": "2026-13", "period_to": "2026-12"},
                       {"period_from": "2026-12", "period_to": "2026-01"}):
            assert (await c.get("/finance/v1/gl/jv-validation", params=params,
                                headers=_h())).status_code == 422
        assert (await c.get("/finance/v1/gl/jv-validation/lines",
                            params={"period_from": "2026-01", "period_to": "2026-12",
                                    "rule": "nope"}, headers=_h())).status_code == 422


async def test_api_requires_auth(client, seeded):
    async with client as c:
        r = await c.get("/finance/v1/gl/jv-validation",
                        params={"period_from": "2026-01", "period_to": "2026-12"})
        assert r.status_code in (401, 403)


# ── the standing Admin Task ──────────────────────────────────────────────────

def _run(today=date(2026, 9, 16)):
    con = psycopg2.connect(_TEST_DSN)
    cur = con.cursor()
    out = tasks.raise_or_clear(cur, uuid.uuid4(), today=today)
    con.commit(); con.close()
    return out


def _open_tasks():
    return _exec("select type, document_type, document_number, assigned_role, title, "
                 "description from tasks where is_completed is false")


async def test_task_raised_then_refreshed_then_closed(db_session, seeded):
    assert _run() == "raised"
    rows = _open_tasks()
    assert len(rows) == 1
    typ, doc_type, number, role, title, description = rows[0]
    assert (typ, doc_type, number, role) == (
        tasks.TASK_TYPE, tasks.DOC_TYPE, tasks.TASK_KEY, tasks.ADMIN_ROLE)
    assert "Budget Dashboard" in title
    assert "DROPPED" in description and "CHECK" in description

    # A second run must refresh the same row, never stack a second one.
    assert _run() == "refreshed"
    assert len(_open_tasks()) == 1

    # Fix the data -> the task closes itself rather than outliving the condition.
    _exec("delete from jv_line_dimensions")
    _exec("delete from journal_voucher_lines")
    assert _run() == "closed"
    assert _open_tasks() == []
    assert _run() == "clean"


async def test_task_scope_is_this_year_and_last(db_session, seeded):
    assert tasks.scope_periods(date(2026, 9, 16)) == ("2025-01", "2026-12")
    # 2026 findings are out of scope for a 2028 run -> nothing raised.
    assert _run(today=date(2028, 1, 1)) == "clean"


async def test_task_failure_does_not_poison_the_transaction(db_session, seeded):
    """The sync must still commit if the reporting query blows up."""
    con = psycopg2.connect(_TEST_DSN)
    cur = con.cursor()
    cur.execute("create temp table probe (x int)")
    broken = "select * from a_table_that_is_not_there"
    orig = svc._SUMMARY_SQL
    try:
        svc._SUMMARY_SQL = broken
        assert tasks.raise_or_clear(cur, uuid.uuid4()) == "failed"
        # The caller's transaction is still usable — this is the whole point.
        cur.execute("insert into probe values (1)")
        con.commit()
    finally:
        svc._SUMMARY_SQL = orig
        con.close()


# ── wired into the sync (a validator nobody calls is worth nothing) ───────────

async def test_nc_sync_run_raises_the_task(db_session):
    """start_run -> the standing task exists, written in the run's own
    transaction. Guards the wiring, not the rules: a correct validator that the
    sync never calls leaves the inbox as silent as before."""
    from app.services.nc_sync import NcExtract, start_run

    _exec("delete from tasks")

    def _extract():
        # Deliberately NOT test_nc_sync's _mini_extract: its income-expense code
        # is CRM004, which is bypassed now, so it would raise nothing and this
        # test would pass while proving the opposite of what it claims.
        return NcExtract(
            ccy={"CADPK": "CAD"},
            aux={"A1": ("0104", "E09", "CRM00201", "", "", "")},   # 'E09' is in no map row
            vouchers=[("VALPK1", "2026", "07", 1, "v", "2026-07-10 09:00:00",
                       "2026-07-11 08:00:00", "2026-07-11 09:00:00", "GL")],
            details=[("VALPK1", 1, "5101", 100, 0, 100, 0, "CADPK", 1, "x", "A1"),
                     ("VALPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "x", "A1")],
            max_creationtime="2026-07-11 08:00:00", tallied={"VALPK1"})

    run_id = start_run("incremental", uuid.uuid4(),
                       fetch=lambda wm: _extract(), pg_dsn=_TEST_DSN)
    assert _exec("select status from nc_sync_runs where id=%s", (run_id,))[0][0] == "success"
    rows = _exec("select type, document_number, document_id from tasks "
                 "where is_completed is false")
    assert len(rows) == 1
    assert rows[0][0] == tasks.TASK_TYPE
    assert rows[0][1] == tasks.TASK_KEY
    assert rows[0][2] == run_id          # anchors on the run that reported it
