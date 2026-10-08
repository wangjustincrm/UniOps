"""Vendor attribution on the Budget Dashboard breakdown.

NC hangs the 客商 aux on the PAYABLE line, not on the expense line, so an
expense line usually names no vendor at all:

    debit  6602    27,212.38   (no party)      <- the actual
    credit 220201  27,500.00   SG Injury Law   <- the vendor

The breakdown sums debit, so the vendor never reached it and 2.2M CAD of 2026
spend read "(no vendor)" on vouchers that name one. These tests pin the fix and,
just as importantly, its limits: a voucher naming several parties must NOT be
split across its expense lines, and a voucher naming none must stay visibly
unattributed rather than borrowing a name from somewhere.
"""
import os
import uuid
from decimal import Decimal

import psycopg2
import pytest

from app.crud import account_balance as crud

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")

VENDOR_X = uuid.UUID("11111111-1111-4111-8111-111111111111")
VENDOR_Y = uuid.UUID("22222222-2222-4222-8222-222222222222")
VENDOR_Z = uuid.UUID("33333333-3333-4333-8333-333333333333")


def _exec(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


@pytest.fixture
def seeded(db_session):
    """Five vouchers, one per attribution case, all on the same budget account
    and cost center so a single breakdown call covers them."""
    item = uuid.uuid4()
    cc = uuid.uuid4()
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "cost_centers", "budget_accounts", "chart_of_accounts"):
        cur.execute(f"delete from {t}")
    cur.execute("insert into chart_of_accounts (id, code, name, account_type, "
                "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
                "created_at, updated_at) values (%s,'6602','G&A','expense','debit',"
                "true,null,true,'[]'::jsonb,now(),now())", (uuid.uuid4(),))
    cur.execute("insert into cost_centers (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'GA-0101','G&A-HR',true,now(),now())", (cc,))
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'CRM00201','Training',true,now(),now())", (item,))

    def voucher(number, lines):
        jv = uuid.uuid4()
        cur.execute(
            "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
            "fiscal_period, summary, status, total_debit, total_credit, "
            "total_local_debit, total_local_credit, created_at, updated_at) "
            "values (%s,%s,'JV','2026-06-15','2026-06',%s,'posted',0,0,0,0,now(),now())",
            (jv, number, number))
        for i, (account, debit, credit, pid, pname, with_item) in enumerate(lines, 1):
            cur.execute(
                "insert into journal_voucher_lines (id, jv_id, line_no, account_code, "
                "summary, orig_debit, orig_credit, local_debit, local_credit, currency, "
                "fx_rate, cost_center_id, income_expense_item_id, partner_id, "
                "partner_name, created_at, updated_at) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'CAD',1,%s,%s,%s,%s,now(),now())",
                (uuid.uuid4(), jv, i, account, number, debit, credit, debit, credit,
                 cc if with_item else None, item if with_item else None, pid, pname))
        return jv

    # The real shape: vendor on the credit (payable) line only.
    voucher("JV-VOUCHER", [("6602", 100, 0, None, None, True),
                           ("220201", 0, 100, VENDOR_X, "Vendor X", False)])
    # Already correct today: the expense line names the vendor itself.
    voucher("JV-LINE", [("6602", 60, 0, VENDOR_Y, "Vendor Y", True),
                        ("220201", 0, 60, VENDOR_Y, "Vendor Y", False)])
    # Two parties: not attributable without inventing an allocation.
    voucher("JV-MULTI", [("6602", 40, 0, None, None, True),
                         ("220201", 0, 20, VENDOR_X, "Vendor X", False),
                         ("220201", 0, 20, VENDOR_Z, "Vendor Z", False)])
    # Internal accrual: no party anywhere.
    voucher("JV-NONE", [("6602", 25, 0, None, None, True),
                        ("2201", 0, 25, None, None, False)])
    # Two vendors NC knows but UniOps has no record for: partner_id NULL, the raw
    # NC code kept as the name. These used to collapse into one "(no vendor)" row
    # displaying whichever name came first.
    voucher("JV-NAMEONLY-A", [("6602", 7, 0, None, None, True),
                              ("220201", 0, 7, None, "NCCODE-A", False)])
    voucher("JV-NAMEONLY-B", [("6602", 3, 0, None, None, True),
                              ("220201", 0, 3, None, "NCCODE-B", False)])
    con.close()
    return {"item": item, "cc": cc}


async def _rows(db_session, seeded):
    res = await crud.nc_partner_monthly(db_session, seeded["item"], 2026)
    return {r["key"]: r for r in res["partners"]}


async def test_vendor_is_taken_from_the_voucher_when_the_line_has_none(db_session, seeded):
    rows = await _rows(db_session, seeded)
    r = rows[str(VENDOR_X)]
    assert r["partner_name"] == "Vendor X"
    assert r["source"] == "voucher"
    assert r["year_total"] == "100.00"
    assert r["inferred_total"] == "100.00"      # all of it came off the voucher


async def test_a_line_that_names_its_own_vendor_is_unchanged(db_session, seeded):
    r = (await _rows(db_session, seeded))[str(VENDOR_Y)]
    assert r["source"] == "line"
    assert r["year_total"] == "60.00"
    assert r["inferred_total"] == "0.00"


async def test_several_parties_on_one_voucher_are_not_guessed_at(db_session, seeded):
    rows = await _rows(db_session, seeded)
    assert "multi" in rows
    assert rows["multi"]["year_total"] == "40.00"
    assert rows["multi"]["partner_name"] is None
    # Neither party may absorb it.
    assert rows[str(VENDOR_X)]["year_total"] == "100.00"
    assert str(VENDOR_Z) not in rows


async def test_a_voucher_with_no_party_stays_unattributed(db_session, seeded):
    rows = await _rows(db_session, seeded)
    assert rows["none"]["year_total"] == "25.00"
    assert rows["none"]["partner_name"] is None


async def test_vendors_with_no_uniops_record_are_separate_rows(db_session, seeded):
    """The old key was `partner_id or '__none__'`, so both of these — and the
    no-party bucket — were one row under a single borrowed name."""
    rows = await _rows(db_session, seeded)
    assert rows["NCCODE-A"]["year_total"] == "7.00"
    assert rows["NCCODE-B"]["year_total"] == "3.00"
    assert rows["NCCODE-A"]["partner_name"] == "NCCODE-A"
    assert rows["none"]["year_total"] == "25.00"    # not merged into these


async def test_the_breakdown_still_sums_to_the_account_total(db_session, seeded):
    """Attribution must move money between rows, never create or lose it."""
    rows = await _rows(db_session, seeded)
    total = sum(Decimal(r["year_total"]) for r in rows.values())
    monthly = await crud.nc_actuals_monthly(db_session, 2026)
    assert total == Decimal(monthly["accounts"][str(seeded["item"])][6])


async def test_drill_filters_on_the_effective_party(db_session, seeded):
    """The drill must find the voucher-attributed line — filtering on the line's
    own partner_id would return nothing for exactly the rows this fix created."""
    res = await crud.nc_partner_vouchers(db_session, seeded["item"], 2026, 6,
                                         partner_id=str(VENDOR_X))
    numbers = {r["jv_number"] for r in res["rows"]}
    assert numbers == {"JV-VOUCHER"}
    row = res["rows"][0]
    assert row["partner_name"] == "Vendor X"
    assert row["partner_source"] == "voucher"
    assert row["line_partner_name"] is None       # the line itself names nobody


async def test_drill_on_the_bucket_keys(db_session, seeded):
    multi = await crud.nc_partner_vouchers(db_session, seeded["item"], 2026, 6,
                                           partner_id="multi")
    assert {r["jv_number"] for r in multi["rows"]} == {"JV-MULTI"}
    none = await crud.nc_partner_vouchers(db_session, seeded["item"], 2026, 6,
                                          partner_id="none")
    assert {r["jv_number"] for r in none["rows"]} == {"JV-NONE"}


async def test_bulk_breakdown_matches_the_single_account_one(db_session, seeded):
    """nc_partner_monthly_all must not drift from nc_partner_monthly — they are
    the same report at two granularities."""
    one = await _rows(db_session, seeded)
    bulk = await crud.nc_partner_monthly_all(db_session, fiscal_year=2026)
    bulk_rows = {r["key"]: r for r in bulk[str(seeded["item"])]}
    assert {k: v["year_total"] for k, v in bulk_rows.items()} == \
           {k: v["year_total"] for k, v in one.items()}
    assert {k: v["source"] for k, v in bulk_rows.items()} == \
           {k: v["source"] for k, v in one.items()}


# ── 6603: budgeted by account, not by income-expense item ────────────────────

@pytest.fixture
def seeded_6603(db_session):
    """A financial-expense voucher in NC's own shape: an account under 6603, no
    income-expense item anywhere, vendor on the payable line."""
    cc = uuid.UUID("44444444-4444-4444-8444-444444444444")
    fn_account = uuid.uuid4()
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor()
    for t in ("jv_line_dimensions", "journal_voucher_lines", "journal_vouchers",
              "cost_centers", "budget_accounts", "chart_of_accounts"):
        cur.execute(f"delete from {t}")
    for code, parent in (("6603", None), ("660303", "6603")):
        cur.execute(
            "insert into chart_of_accounts (id, code, name, account_type, "
            "normal_balance, is_postable, parent_code, is_active, aux_dimensions, "
            "created_at, updated_at) values (%s,%s,%s,'expense','debit',true,%s,true,"
            "'[]'::jsonb, now(), now())", (uuid.uuid4(), code, code, parent))
    cur.execute("insert into cost_centers (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'FN-0103','FIN-FinanceExpense',true,now(),now())", (cc,))
    # The budget account IS the accounting account, same code.
    cur.execute("insert into budget_accounts (id, code, name, is_active, created_at, "
                "updated_at) values (%s,'660303','Bank Charge',true,now(),now())", (fn_account,))
    jv = uuid.uuid4()
    cur.execute(
        "insert into journal_vouchers (id, jv_number, voucher_word, voucher_date, "
        "fiscal_period, summary, status, total_debit, total_credit, total_local_debit, "
        "total_local_credit, created_at, updated_at) values "
        "(%s,'JV-FN-0001','JV','2026-06-15','2026-06','bank charge','posted',0,0,0,0,now(),now())",
        (jv,))
    for no, account, debit, credit, pid, pname, with_cc in (
            (1, "660303", 90, 0, None, None, True),
            (2, "220201", 0, 90, VENDOR_X, "Vendor X", False)):
        cur.execute(
            "insert into journal_voucher_lines (id, jv_id, line_no, account_code, summary, "
            "orig_debit, orig_credit, local_debit, local_credit, currency, fx_rate, "
            "cost_center_id, income_expense_item_id, partner_id, partner_name, "
            "created_at, updated_at) "
            "values (%s,%s,%s,%s,'bank charge',%s,%s,%s,%s,'CAD',1,%s,null,%s,%s,now(),now())",
            (uuid.uuid4(), jv, no, account, debit, credit, debit, credit,
             cc if with_cc else None, pid, pname))
    con.close()
    return {"cc": cc, "fn_account": fn_account}


async def test_6603_actual_is_keyed_by_the_accounting_account(db_session, seeded_6603):
    """FN-0103 read zero actual for every month of every year while 97,182.57
    sat in the books: 6603 lines carry no income-expense item, so the INNER join
    that keys the dashboard dropped all of them."""
    res = await crud.nc_actuals_monthly(db_session, 2026, seeded_6603["cc"])
    assert res["accounts"][str(seeded_6603["fn_account"])] == {6: "90.00"}


async def test_6603_drill_finds_its_vendor(db_session, seeded_6603):
    """The drill must key the same way the cell does — and the vendor still
    comes off the voucher, since the expense line names none."""
    res = await crud.nc_partner_monthly(db_session, seeded_6603["fn_account"], 2026)
    rows = {r["key"]: r for r in res["partners"]}
    assert rows[str(VENDOR_X)]["year_total"] == "90.00"
    assert rows[str(VENDOR_X)]["source"] == "voucher"

    vouchers = await crud.nc_partner_vouchers(
        db_session, seeded_6603["fn_account"], 2026, 6, partner_id=str(VENDOR_X))
    assert {r["jv_number"] for r in vouchers["rows"]} == {"JV-FN-0001"}
