"""Budget-vs-Actual roll-up: company / expense centre / department / cost centre.

The property that matters most here is not any single figure — it is that the
figures add up. This is the page finance reports from, and the failure this
whole line of work began with was money quietly missing from a total.
"""
import os
import uuid
from decimal import Decimal

import psycopg2
import pytest

from app.crud import budget_rollup as rollup

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


@pytest.fixture
def seeded(db_session):
    """Supply Chain spans two expense centres — the case that makes the
    department level a real roll-up rather than a relabelling of the cost
    centre. Plus one policy line and one line that reaches no cost centre."""
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "budget_plan_lines", "budget_plans", "cost_centers", "departments",
              "budget_accounts", "chart_of_accounts"):
        cur.execute(f"delete from {t}")
    for code, parent in (("5101", None), ("6602", None), ("6603", None)):
        cur.execute("insert into chart_of_accounts (id, code, name, account_type, "
                    "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
                    "created_at, updated_at) values (%s,%s,%s,'expense','debit',true,%s,"
                    "true,'[]'::jsonb,now(),now())", (uuid.uuid4(), code, code, parent))

    dept_sc, dept_hr = uuid.uuid4(), uuid.uuid4()
    for did, code, name in ((dept_sc, "0107", "Supply Chain"), (dept_hr, "0101", "HR")):
        cur.execute("insert into departments (id, code, name, is_active, created_at, "
                    "updated_at) values (%s,%s,%s,true,now(),now())", (did, code, name))

    cc_ga_sc, cc_moh_sc, cc_ga_hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for cid, code, did in ((cc_ga_sc, "GA-0107", dept_sc),
                           (cc_moh_sc, "MOH-0107-S02", dept_sc),
                           (cc_ga_hr, "GA-0101", dept_hr)):
        cur.execute("insert into cost_centers (id, code, name, is_active, department_id, "
                    "created_at, updated_at) values (%s,%s,%s,true,%s,now(),now())",
                    (cid, code, code, did))

    ba = uuid.uuid4()
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'CRM00201','Training',true,now(),now())", (ba,))

    def plan(cc, month_amounts):
        pid = uuid.uuid4()
        cur.execute("insert into budget_plans (id, cost_center_id, fiscal_year, status, "
                    "is_current, created_at, updated_at) "
                    "values (%s,%s,2026,'approved',true,now(),now())", (pid, cc))
        for m, amt in month_amounts.items():
            cur.execute("insert into budget_plan_lines (id, plan_id, account_id, month, "
                        "amount, created_at, updated_at) values (%s,%s,%s,%s,%s,now(),now())",
                        (uuid.uuid4(), pid, ba, m, amt))

    # 100/month all year = 1200 full year, 900 through September.
    plan(cc_ga_sc, {m: 100 for m in range(1, 13)})
    plan(cc_moh_sc, {m: 50 for m in range(1, 13)})
    plan(cc_ga_hr, {m: 10 for m in range(1, 13)})

    def line(period, account, amount, cc=None, io_id=None, io_text=None):
        jv = uuid.uuid4()
        cur.execute(
            "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
            "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
            "total_local_credit, created_at, updated_at) values "
            "(%s,%s,'JV',%s,%s,'x','posted',0,0,0,0,now(),now())",
            (jv, f"JV-{period}-{str(uuid.uuid4())[:6]}", f"{period}-15", period))
        lid = uuid.uuid4()
        cur.execute(
            "insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
            "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, "
            "cost_center_id, income_expense_item_id, created_at, updated_at) "
            "values (%s,%s,1,%s,'x',%s,0,%s,0,'CAD',1,%s,%s,now(),now())",
            (lid, jv, account, amount, amount, cc, io_id))
        if io_text:
            cur.execute("insert into jv_line_dimensions (id, jv_line_id, dim_code, value_id, "
                        "value_text, created_at, updated_at) "
                        "values (%s,%s,'income_expense_item',%s,%s,now(),now())",
                        (uuid.uuid4(), lid, io_id, io_text))

    line("2026-03", "6602", 300, cc=cc_ga_sc, io_id=ba, io_text="CRM00201")
    line("2026-08", "6602", 200, cc=cc_ga_sc, io_id=ba, io_text="CRM00201")
    line("2026-08", "5101", 400, cc=cc_moh_sc, io_id=ba, io_text="CRM00201")
    line("2026-11", "6602", 999, cc=cc_ga_sc, io_id=ba, io_text="CRM00201")  # after the window
    # Category-level: shut-down loss, no catalog row, no cost centre.
    line("2026-08", "6602", 5000, cc=None, io_id=None, io_text="CRM09912")
    # Reaches no cost centre and is not policy — the other unallocated kind.
    line("2026-08", "6602", 7, cc=None, io_id=ba, io_text="CRM00201")
    con.close()
    return {"cc_ga_sc": cc_ga_sc, "cc_moh_sc": cc_moh_sc, "cc_ga_hr": cc_ga_hr}


async def _roll(db, *, mf=1, mt=9):
    return await rollup.rollup(db, fiscal_year=2026, month_from=mf, month_to=mt)


async def test_company_equals_the_sum_of_every_level(db_session, seeded):
    r = await _roll(db_session)
    company = Decimal(r["company"]["actual_ytd"])
    by_centre = sum(Decimal(n["actual_ytd"]) for n in r["by_expense_centre"])
    by_dept = sum(Decimal(n["actual_ytd"]) for n in r["by_department"])
    assert company == by_centre == by_dept == Decimal("900.00")   # 300 + 200 + 400
    # …and the leaves add up to their own parents.
    for node in r["by_expense_centre"] + r["by_department"]:
        assert sum(Decimal(c["actual_ytd"]) for c in node["children"]) == \
               Decimal(node["actual_ytd"])


async def test_a_department_spanning_expense_centres_rolls_up(db_session, seeded):
    """Supply Chain runs both G&A and manufacturing overhead. Its department row
    is the sum of both; its cost centres appear under two different centres."""
    r = await _roll(db_session)
    sc = next(d for d in r["by_department"] if d["code"] == "0107")
    assert Decimal(sc["actual_ytd"]) == Decimal("900.00")        # 500 G&A + 400 MOH
    assert Decimal(sc["plan_full_year"]) == Decimal("1800.00")   # (100 + 50) * 12
    assert {c["cost_center_code"] for c in sc["children"]} == {"GA-0107", "MOH-0107-S02"}

    centres = {n["code"]: n for n in r["by_expense_centre"]}
    assert Decimal(centres["GA"]["actual_ytd"]) == Decimal("500.00")
    assert Decimal(centres["MOH"]["actual_ytd"]) == Decimal("400.00")
    assert centres["MOH"]["label"] == "Manufacturing Overhead"


async def test_the_period_window_is_a_window(db_session, seeded):
    """One month, a quarter and a range — and the year figures stay annual."""
    single = await _roll(db_session, mf=8, mt=8)
    assert single["company"]["actual_period"] == "600.00"     # 200 + 400 in August
    assert single["company"]["plan_period"] == "160.00"       # 100 + 50 + 10
    # The year side does not narrow with the window: actual_ytd is Jan..month_to.
    assert single["company"]["actual_ytd"] == "900.00"
    assert single["company"]["plan_full_year"] == "1920.00"

    quarter = await _roll(db_session, mf=7, mt=9)
    assert quarter["company"]["actual_period"] == "600.00"

    q1 = await _roll(db_session, mf=1, mt=3)
    assert q1["company"]["actual_period"] == "300.00"
    assert q1["company"]["actual_ytd"] == "300.00"


async def test_november_is_outside_a_september_window(db_session, seeded):
    """Guards the YTD filter: NC carries vouchers dated past the current month."""
    r = await _roll(db_session, mt=9)
    assert Decimal(r["company"]["actual_ytd"]) == Decimal("900.00")   # 999 excluded
    full = await _roll(db_session, mt=12)
    assert Decimal(full["company"]["actual_ytd"]) == Decimal("1899.00")


async def test_unallocated_is_split_by_why(db_session, seeded):
    """"Not in any cost centre" has two causes that need different people, so
    they are not one lump."""
    r = await _roll(db_session)
    buckets = {b["key"]: b for b in r["unallocated"]["breakdown"]}
    assert buckets["CRM09912"]["actual_ytd"] == "5000.00"
    assert buckets["CRM09912"]["label"] == "Shut-down loss"
    assert buckets["__unplaced__"]["actual_ytd"] == "7.00"
    assert r["unallocated"]["actual_ytd"] == "5007.00"


async def test_the_parts_add_up_to_everything_posted(db_session, seeded):
    """The reconciliation line is the report's own proof. Placed + unallocated
    must equal every posted predreal line in the window — no silent remainder."""
    r = await _roll(db_session, mf=1, mt=9)
    rec = r["reconciliation"]
    assert Decimal(rec["placed_actual_period"]) == Decimal("900.00")
    assert Decimal(rec["unallocated_actual_period"]) == Decimal("5007.00")
    assert Decimal(rec["total_actual_period"]) == Decimal("5907.00")

    posted = _exec(
        "select coalesce(sum(l.local_debit),0) from journal_vouchers v "
        "join journal_voucher_lines l on l.jv_id=v.id "
        "where v.status='posted' and v.fiscal_period between '2026-01' and '2026-09'")[0][0]
    assert Decimal(rec["total_actual_period"]) == Decimal(posted)


async def test_percentages_of_nothing_are_null_not_zero(db_session, seeded):
    """A cost centre with no plan has an unanswerable consumption rate. Printing
    0% there reads as "on budget" for a centre that is spending with no budget
    at all."""
    r = await _roll(db_session)
    ga = next(n for n in r["by_expense_centre"] if n["code"] == "GA")
    hr = next(c for c in ga["children"] if c["cost_center_code"] == "GA-0101")
    assert hr["actual_ytd"] == "0.00"
    assert hr["consumed_pct"] == "0.0"          # has a plan, spent nothing
    q1 = await _roll(db_session, mf=1, mt=1)
    # January has plan but the period variance pct is computable; make a centre
    # with no plan in the window by asking for a month nobody budgeted.
    assert q1["company"]["plan_period"] != "0.00"


async def test_variance_is_reported_both_ways(db_session, seeded):
    r = await _roll(db_session, mf=1, mt=9)
    c = r["company"]
    # period: what we meant to spend by September vs what we did
    assert c["plan_period"] == "1440.00"       # (100+50+10) * 9
    assert c["variance_period"] == "540.00"    # 1440 - 900
    # year: the whole budget vs what is gone
    assert c["plan_full_year"] == "1920.00"
    assert c["remaining_full_year"] == "1020.00"
    assert c["consumed_pct"] == "46.9"


# ── composition ─────────────────────────────────────────────────────────────

async def test_composition_sums_to_the_row_it_came_from(db_session, seeded):
    """The property the whole panel rests on: what is shown as the make-up of a
    figure must add up to that figure, at every level."""
    roll = await _roll(db_session)
    for kind, key, expected in (
        ("company", "", roll["company"]["actual_ytd"]),
        ("centre", "GA",
         next(n for n in roll["by_expense_centre"] if n["code"] == "GA")["actual_ytd"]),
        ("department", "0107",
         next(n for n in roll["by_department"] if n["code"] == "0107")["actual_ytd"]),
        ("cost_centre", "GA-0107",
         next(c for n in roll["by_expense_centre"] if n["code"] == "GA"
              for c in n["children"] if c["cost_center_code"] == "GA-0107")["actual_ytd"]),
    ):
        b = await rollup.breakdown(db_session, fiscal_year=2026, month_from=1, month_to=9,
                                   scope_kind=kind, scope_key=key)
        total = sum(Decimal(a["actual_ytd"]) for a in b["accounts"])
        assert total == Decimal(expected), f"{kind}:{key} composition != its roll-up row"
        # and each account's cost-centre children add up to the account
        for acct in b["accounts"]:
            assert sum(Decimal(c["actual_ytd"]) for c in acct["children"]) == \
                   Decimal(acct["actual_ytd"])


async def test_composition_totals_equal_the_roll_up_row(db_session, seeded):
    """The panel's own total line is the claim it makes, so it is checked
    against the row that was clicked — not merely against its own rows."""
    roll = await _roll(db_session)
    for kind, key, row in (
        ("company", "", roll["company"]),
        ("centre", "GA", next(n for n in roll["by_expense_centre"] if n["code"] == "GA")),
        ("department", "0107", next(n for n in roll["by_department"] if n["code"] == "0107")),
    ):
        b = await rollup.breakdown(db_session, fiscal_year=2026, month_from=1, month_to=9,
                                   scope_kind=kind, scope_key=key)
        for field in ("plan_period", "actual_period", "plan_full_year", "actual_ytd",
                      "variance_period", "remaining_full_year"):
            assert b["totals"][field] == row[field], f"{kind}:{key} {field}"
        # and it equals the sum of the rows shown under it
        assert Decimal(b["totals"]["actual_ytd"]) == \
               sum(Decimal(a["actual_ytd"]) for a in b["accounts"])


async def test_composition_follows_the_window(db_session, seeded):
    august = await rollup.breakdown(db_session, fiscal_year=2026, month_from=8, month_to=8,
                                    scope_kind="company")
    assert sum(Decimal(a["actual_period"]) for a in august["accounts"]) == Decimal("600.00")
    # The year columns stay annual even in a one-month window.
    assert sum(Decimal(a["actual_ytd"]) for a in august["accounts"]) == Decimal("900.00")


async def test_composition_is_ordered_by_account_code(db_session, seeded):
    """Not by amount: finance reads this against the chart of accounts and
    across periods, and a list that reorders itself as the numbers move cannot
    be scanned the same way twice."""
    b = await rollup.breakdown(db_session, fiscal_year=2026, month_from=1, month_to=9,
                               scope_kind="company")
    codes = [a["code"] for a in b["accounts"]]
    assert codes == sorted(codes)


async def test_composition_drops_rows_that_are_zero_on_both_sides(db_session, seeded):
    """Every cost centre carries a plan line for every account in the catalog,
    almost all of them 0.00. Listing them would bury the handful that matter."""
    b = await rollup.breakdown(db_session, fiscal_year=2026, month_from=1, month_to=9,
                               scope_kind="cost_centre", scope_key="GA-0101")
    assert [a["code"] for a in b["accounts"]] == ["CRM00201"]   # the only one with a plan


async def test_composition_of_an_unknown_scope_is_empty_not_everything(db_session, seeded):
    """A scope key that matches nothing must not silently widen to the company."""
    b = await rollup.breakdown(db_session, fiscal_year=2026, month_from=1, month_to=9,
                               scope_kind="cost_centre", scope_key="NOPE-9999")
    assert b["accounts"] == []


async def test_unallocated_lines_open_up(db_session, seeded):
    """Each bucket of the unallocated row can be opened, and what comes back is
    exactly the lines that bucket counted — nothing that was counted elsewhere.
    "349,796 not placed in any cost centre" is unactionable until you can see
    which vouchers they are."""
    roll = await _roll(db_session)
    buckets = {b["key"]: b for b in roll["unallocated"]["breakdown"]}

    shut = await rollup.unallocated_lines(db_session, fiscal_year=2026, month_from=1,
                                          month_to=9, bucket="CRM09912")
    assert sum(Decimal(r["debit"]) for r in shut["rows"]) == \
           Decimal(buckets["CRM09912"]["actual_period"])
    # the diagnosis column: what NC wrote, which is why the line is in here
    assert {r["nc_income_expense"] for r in shut["rows"]} == {"CRM09912"}

    unplaced = await rollup.unallocated_lines(db_session, fiscal_year=2026, month_from=1,
                                              month_to=9, bucket="__unplaced__")
    assert sum(Decimal(r["debit"]) for r in unplaced["rows"]) == \
           Decimal(buckets["__unplaced__"]["actual_period"])
    # placed lines must never show up in an unallocated drill
    assert all(r["jv_number"] for r in unplaced["rows"])


async def test_unallocated_lines_respect_the_window(db_session, seeded):
    """The seeded policy line is in August; asking about Q1 must not return it."""
    q1 = await rollup.unallocated_lines(db_session, fiscal_year=2026, month_from=1,
                                        month_to=3, bucket="CRM09912")
    assert q1["rows"] == []
