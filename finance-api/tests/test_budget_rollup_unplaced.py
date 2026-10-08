"""What lands in the unallocated bucket, and what the drill-down says about it.

Two defects found by a user reading the page on 2026-09-19, both about the
`__unplaced__` bucket:

1. It is `cost_center_id is null OR budget_account_id is null`, but was labelled
   "Not placed in any cost centre" -- false for the 4,199 FY2026 lines that do
   have a cost centre and are only missing the budget account.
2. `budget_account_id` resolved solely from `income_expense_item_id`. NC often
   sends the income/expense item as dimension TEXT with no resolved id, so such
   a line was called unplaced while the same query printed the very code that
   would have placed it -- and the policy-exclusion match right beside it
   already trusted that same text.
"""
import os
import uuid
from decimal import Decimal

import psycopg2

from app.crud import budget_rollup as rollup

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _seed():
    """One cost centre, one budget account, four lines -- one per case."""
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "budget_plan_lines", "budget_plans", "cost_centers", "departments",
              "budget_accounts", "chart_of_accounts"):
        cur.execute(f"delete from {t}")
    cur.execute("insert into chart_of_accounts (id, code, name, account_type, "
                "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
                "created_at, updated_at) values (%s,'6602','6602','expense','debit',"
                "true,null,true,'[]'::jsonb,now(),now())", (uuid.uuid4(),))
    dept = uuid.uuid4()
    cur.execute("insert into departments (id, code, name, is_active, created_at, updated_at) "
                "values (%s,'0106','Engineering',true,now(),now())", (dept,))
    cc = uuid.uuid4()
    cur.execute("insert into cost_centers (id, code, name, is_active, department_id, "
                "created_at, updated_at) values (%s,'GA-0106','GA-0106',true,%s,now(),now())",
                (cc, dept))
    ba = uuid.uuid4()
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'CRM00201','Training',true,now(),now())", (ba,))

    def line(amount, *, cost_centre, io_id, io_text):
        jv = uuid.uuid4()
        cur.execute(
            "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
            "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
            "total_local_credit, created_at, updated_at) values "
            "(%s,%s,'JV','2026-08-15','2026-08','x','posted',0,0,0,0,now(),now())",
            (jv, f"JV-{str(uuid.uuid4())[:8]}"))
        lid = uuid.uuid4()
        cur.execute(
            "insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
            "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, "
            "cost_center_id, department_id, income_expense_item_id, created_at, updated_at) "
            "values (%s,%s,1,'6602','x',%s,0,%s,0,'CAD',1,%s,%s,%s,now(),now())",
            (lid, jv, amount, amount, cost_centre, dept, io_id))
        if io_text is not None:
            cur.execute("insert into jv_line_dimensions (id, jv_line_id, dim_code, value_id, "
                        "value_text, created_at, updated_at) "
                        "values (%s,%s,'income_expense_item',%s,%s,now(),now())",
                        (uuid.uuid4(), lid, io_id, io_text))

    # (a) fully resolved — the ordinary placed line.
    line(100, cost_centre=cc, io_id=ba, io_text="CRM00201")
    # (b) cost centre, no FK, but the dimension text names a real account.
    #     This is JV-202608-0409 #1 in production.
    line(65, cost_centre=cc, io_id=None, io_text="CRM00201")
    # (c) cost centre, and a text naming nothing — genuinely unplaceable.
    line(11, cost_centre=cc, io_id=None, io_text="NOSUCHCODE")
    # (d) no cost centre at all, account resolved — the other fault.
    line(7, cost_centre=None, io_id=ba, io_text="CRM00201")
    con.close()


async def _roll(db):
    _seed()
    return await rollup.rollup(db, fiscal_year=2026, month_from=1, month_to=9)


def _unplaced(r):
    return next(b for b in r["unallocated"]["breakdown"] if b["key"] == "__unplaced__")


async def test_a_line_placed_only_by_its_dimension_text_counts_as_placed(db_session):
    """(a) + (b) reach the company total; (c) and (d) do not."""
    r = await _roll(db_session)
    assert Decimal(r["company"]["actual_ytd"]) == Decimal("165.00")     # 100 + 65
    assert Decimal(_unplaced(r)["actual_ytd"]) == Decimal("18.00")      # 11 + 7


async def test_a_text_naming_no_account_is_still_unplaced(db_session):
    """The admission half of the test above. Resolving from text must not
    become 'anything with a dimension row is placed' -- (c) has one and is
    still unplaceable, which is the only reason the 18.00 above is not 7.00."""
    r = await _roll(db_session)
    rows = (await rollup.unallocated_lines(
        db_session, fiscal_year=2026, month_from=1, month_to=9,
        bucket="__unplaced__"))["rows"]
    texts = {row["nc_income_expense"] for row in rows}
    assert "NOSUCHCODE" in texts
    assert "CRM00201" in texts          # that is (d), missing its cost centre
    assert len(rows) == 2


async def test_the_totals_still_close(db_session):
    """The property the whole page rests on: placed + unallocated == everything
    those lines are worth. Moving a line between the two sides must not create
    or destroy money."""
    r = await _roll(db_session)
    rec = r["reconciliation"]
    assert (Decimal(rec["placed_actual_period"])
            + Decimal(rec["unallocated_actual_period"])
            == Decimal(rec["total_actual_period"]) == Decimal("183.00"))  # 100+65+11+7


async def test_the_drill_down_says_which_fault_put_each_line_there(db_session):
    """One bucket, two faults. Without this the reader sees a cost-centre
    complaint against a line that has one and concludes the report is broken."""
    r = await _roll(db_session)
    assert r is not None
    rows = (await rollup.unallocated_lines(
        db_session, fiscal_year=2026, month_from=1, month_to=9,
        bucket="__unplaced__"))["rows"]
    by_text = {row["nc_income_expense"]: row for row in rows}

    # (c) has a cost centre; only the account is missing.
    assert by_text["NOSUCHCODE"]["missing_cost_centre"] is False
    assert by_text["NOSUCHCODE"]["missing_budget_account"] is True
    # (d) is the mirror image.
    assert by_text["CRM00201"]["missing_cost_centre"] is True
    assert by_text["CRM00201"]["missing_budget_account"] is False


async def test_the_bucket_label_names_both_faults(db_session):
    r = await _roll(db_session)
    label = _unplaced(r)["label"]
    assert "cost centre" in label and "budget account" in label
