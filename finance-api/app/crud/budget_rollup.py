"""Budget-vs-Actual roll-up — the finance-facing summary above the detail grid.

The Budget Dashboard answers "how is my department doing"; this answers "what do
I report". Same ledger, three levels of aggregation, and the one thing a report
needs that a dashboard does not: the parts must add up to the whole, visibly.

Levels
------
company              one row
expense centre       MOH / RD / SELL / GA / FN — taken from the cost centre CODE
                     prefix (user, 2026-09-18), not from the accounting account.
                     The two agree everywhere except a plan line like GA-0103's
                     660398, which the code prefix puts in G&A.
department           a department can span expense centres — Supply Chain runs
                     G&A, manufacturing overhead AND selling — so this is a real
                     roll-up, not a relabelling. Every cost centre carries a
                     department (verified: 19 of 19).
cost centre          the leaf, reachable by expanding either tree.

Measures
--------
Two pairs, because one of them alone misleads (user asked for both):

  period   plan vs actual over [month_from, month_to] — "did we spend what we
           meant to this quarter". Works for a single month, a quarter, or any
           range.
  year     full-year plan vs actual-to-date — "how much of the year's budget is
           gone". R&D shows why both are needed: 8.7% of the year consumed reads
           as thrift until you see it is 9.6% of what the year-to-date plan
           called for.

Unallocated
-----------
Two things never reach a cost centre, and a report that silently drops them is
the failure this whole line of work started from:

  category-level   payroll, depreciation and shut-down loss, which finance
                   tracks by category and does not spread over cost centres.
  no cost centre   lines whose (account, department, NC cost centre) combination
                   the map does not cover — small today (72.58), never zero by
                   construction.

`reconciliation` proves the arithmetic closes: company actual + unallocated ==
every posted predreal line in the period.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.crud.account_balance import (
    BUDGET_ACTUAL_ACCOUNTS,
    EXCLUDED_IO_LABELS,
    EXCLUDED_IO_PREFIXES,
)

_ZERO = Decimal("0")

#: Cost-centre code prefix -> what finance calls it. Same five as
#: BUDGET_ACTUAL_ACCOUNTS' values, named here because this page groups by the
#: cost centre, not by the account.
EXPENSE_CENTRE_LABELS: dict[str, str] = {
    "MOH": "Manufacturing Overhead",
    "RD": "R&D",
    "SELL": "Selling",
    "GA": "G&A",
    "FN": "Financial",
}

_CATEGORIES = ", ".join(f"('{a}')" for a in BUDGET_ACTUAL_ACCOUNTS)

# One query, one pass per cost centre. The period/YTD split is done with FILTER
# rather than by running the query twice.
_SQL = """
with recursive cat(code) as (
    select code from (values {categories}) as h(code)
    union all
    select c.code from chart_of_accounts c join cat on c.parent_code = cat.code
),
fn(code) as (
    select code from chart_of_accounts where code = '6603'
    union all
    select c.code from chart_of_accounts c join fn on c.parent_code = fn.code
),
lines as (
    select l.cost_center_id,
           cast(substr(v.fiscal_period, 6, 2) as int) as m,
           l.local_debit as amt,
           -- Policy items are recognised off EITHER the NC code or the resolved
           -- one: shut-down loss has no catalog row, and a line whose dimension
           -- row is missing has only the resolved code.
           coalesce(dim.value_text, ba.code, '') as nc_code,
           coalesce(ba_acct.id, ba.id) as budget_account_id
      from journal_vouchers v
      join journal_voucher_lines l on l.jv_id = v.id
      join cat on cat.code = l.account_code
      left join budget_accounts ba on ba.id = l.income_expense_item_id
      left join budget_accounts ba_acct
             on ba_acct.code = l.account_code and l.account_code in (select code from fn)
      left join jv_line_dimensions dim
             on dim.jv_line_id = l.id and dim.dim_code = 'income_expense_item'
     where v.status = 'posted'
       and v.fiscal_period like :year_like
),
classified as (
    select *,
           (select p from unnest(cast(:excluded as text[])) p
             where nc_code like p || '%' limit 1) as policy_prefix
      from lines
),
actual as (
    select cost_center_id,
           coalesce(sum(amt) filter (where m between :month_from and :month_to), 0) as period,
           coalesce(sum(amt) filter (where m <= :month_to), 0) as ytd
      from classified
     where policy_prefix is null and budget_account_id is not null
       and cost_center_id is not null
     group by cost_center_id
),
policy as (
    select policy_prefix,
           coalesce(sum(amt) filter (where m between :month_from and :month_to), 0) as period,
           coalesce(sum(amt) filter (where m <= :month_to), 0) as ytd
      from classified
     where policy_prefix is not null
     group by policy_prefix
),
orphan as (
    select coalesce(sum(amt) filter (where m between :month_from and :month_to), 0) as period,
           coalesce(sum(amt) filter (where m <= :month_to), 0) as ytd
      from classified
     where policy_prefix is null
       and (cost_center_id is null or budget_account_id is null)
),
plan as (
    select p.cost_center_id,
           coalesce(sum(pl.amount), 0) as full_year,
           coalesce(sum(pl.amount) filter (where pl.month between :month_from and :month_to), 0) as period
      from budget_plans p
      join budget_plan_lines pl on pl.plan_id = p.id
      join budget_accounts ba on ba.id = pl.account_id
     where p.fiscal_year = :fiscal_year
       and p.status = 'approved' and p.is_current
       and not (ba.code like any(cast(:excluded_like as text[])))
     group by p.cost_center_id
)
select cc.id, cc.code, cc.name,
       dp.code as dept_code, dp.name as dept_name,
       coalesce(plan.full_year, 0), coalesce(plan.period, 0),
       coalesce(actual.period, 0), coalesce(actual.ytd, 0)
  from cost_centers cc
  left join departments dp on dp.id = cc.department_id
  left join actual on actual.cost_center_id = cc.id
  left join plan on plan.cost_center_id = cc.id
 where cc.is_active
 order by cc.code
"""

_POLICY_SQL = """
with recursive cat(code) as (
    select code from (values {categories}) as h(code)
    union all
    select c.code from chart_of_accounts c join cat on c.parent_code = cat.code
),
fn(code) as (
    select code from chart_of_accounts where code = '6603'
    union all
    select c.code from chart_of_accounts c join fn on c.parent_code = fn.code
),
lines as (
    select cast(substr(v.fiscal_period, 6, 2) as int) as m,
           l.local_debit as amt,
           coalesce(dim.value_text, ba.code, '') as nc_code,
           coalesce(ba_acct.id, ba.id) as budget_account_id,
           l.cost_center_id
      from journal_vouchers v
      join journal_voucher_lines l on l.jv_id = v.id
      join cat on cat.code = l.account_code
      left join budget_accounts ba on ba.id = l.income_expense_item_id
      left join budget_accounts ba_acct
             on ba_acct.code = l.account_code and l.account_code in (select code from fn)
      left join jv_line_dimensions dim
             on dim.jv_line_id = l.id and dim.dim_code = 'income_expense_item'
     where v.status = 'posted' and v.fiscal_period like :year_like
),
classified as (
    select *, (select p from unnest(cast(:excluded as text[])) p
                where nc_code like p || '%' limit 1) as policy_prefix
      from lines
)
select coalesce(policy_prefix, '__unplaced__') as bucket,
       coalesce(sum(amt) filter (where m between :month_from and :month_to), 0) as period,
       coalesce(sum(amt) filter (where m <= :month_to), 0) as ytd
  from classified
 where policy_prefix is not null
    or cost_center_id is null or budget_account_id is null
 group by 1
"""


# The lines behind one unallocated bucket. Same classification as the summary
# (nothing may appear here that was counted elsewhere), plus enough of the
# voucher to act on it: which document, which account, which department, and
# what NC actually wrote in the income-expense slot — because for the
# "not placed" bucket that value IS the diagnosis.
_UNALLOCATED_LINES_SQL = """
with recursive cat(code) as (
    select code from (values {categories}) as h(code)
    union all
    select c.code from chart_of_accounts c join cat on c.parent_code = cat.code
),
fn(code) as (
    select code from chart_of_accounts where code = '6603'
    union all
    select c.code from chart_of_accounts c join fn on c.parent_code = fn.code
),
lines as (
    select v.id as jv_id, v.jv_number, v.voucher_date, v.fiscal_period,
           l.line_no, l.account_code, l.summary as line_summary, v.summary as jv_summary,
           l.local_debit, l.cost_center_id,
           dp.code as dept_code,
           coalesce(dim.value_text, ba.code, '') as nc_code,
           dim.value_text as nc_io_text,
           coalesce(ba_acct.id, ba.id) as budget_account_id,
           cast(substr(v.fiscal_period, 6, 2) as int) as m
      from journal_vouchers v
      join journal_voucher_lines l on l.jv_id = v.id
      join cat on cat.code = l.account_code
      left join departments dp on dp.id = l.department_id
      left join budget_accounts ba on ba.id = l.income_expense_item_id
      left join budget_accounts ba_acct
             on ba_acct.code = l.account_code and l.account_code in (select code from fn)
      left join jv_line_dimensions dim
             on dim.jv_line_id = l.id and dim.dim_code = 'income_expense_item'
     where v.status = 'posted' and v.fiscal_period like :year_like
),
classified as (
    select *, (select p from unnest(cast(:excluded as text[])) p
                where nc_code like p || '%' limit 1) as policy_prefix
      from lines
)
select jv_id, jv_number, voucher_date, fiscal_period, line_no, account_code,
       coalesce(line_summary, jv_summary), local_debit, dept_code, nc_io_text
  from classified
 where m between :month_from and :month_to
   and case when :bucket = '__unplaced__'
            then policy_prefix is null
                 and (cost_center_id is null or budget_account_id is null)
            else policy_prefix = :bucket end
 order by fiscal_period, jv_number, line_no
 limit :limit offset :offset
"""


async def unallocated_lines(db, *, fiscal_year: int, month_from: int, month_to: int,
                            bucket: str, limit: int = 200,
                            offset: int = 0) -> dict[str, Any]:
    """Voucher lines behind one bucket of the unallocated row."""
    params = {
        "year_like": f"{fiscal_year}-%", "month_from": month_from, "month_to": month_to,
        "bucket": bucket, "limit": limit, "offset": offset,
        "excluded": list(EXCLUDED_IO_PREFIXES),
    }
    rows = (await db.execute(
        text(_UNALLOCATED_LINES_SQL.format(categories=_CATEGORIES)), params)).all()
    return {
        "bucket": bucket, "limit": limit, "offset": offset,
        "rows": [{
            "jv_id": str(r[0]), "jv_number": r[1],
            "voucher_date": r[2].isoformat() if r[2] else None,
            "fiscal_period": r[3], "line_no": r[4], "account_code": r[5],
            "summary": r[6], "debit": _s(r[7]),
            "department_code": r[8], "nc_income_expense": r[9],
        } for r in rows],
    }


def _s(v) -> str:
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


def _pct(num: Decimal, den: Decimal) -> str | None:
    """One decimal place, or None when the denominator is zero — a percentage of
    nothing is not 0%, it is unanswerable, and printing 0% reads as 'on budget'."""
    if den == 0:
        return None
    return str((num / den * 100).quantize(Decimal("0.1")))


def _metrics(plan_fy: Decimal, plan_period: Decimal,
             actual_period: Decimal, actual_ytd: Decimal) -> dict[str, Any]:
    return {
        # period window [month_from, month_to]
        "plan_period": _s(plan_period),
        "actual_period": _s(actual_period),
        "variance_period": _s(plan_period - actual_period),
        "variance_period_pct": _pct(plan_period - actual_period, plan_period),
        # the year
        "plan_full_year": _s(plan_fy),
        "actual_ytd": _s(actual_ytd),
        "remaining_full_year": _s(plan_fy - actual_ytd),
        "consumed_pct": _pct(actual_ytd, plan_fy),
    }


def _accumulate(bucket: dict, row) -> None:
    for i, key in enumerate(("fy", "pp", "ap", "ay")):
        bucket[key] = bucket.get(key, _ZERO) + Decimal(row[i])


# ── composition: what a roll-up figure is made of ───────────────────────────
#
# Same window, same rules, one level down: budget account × cost centre inside
# whatever the reader clicked. Sharing `_SQL`'s shape matters more than saving
# a few lines — a composition that sums differently from the total above it is
# worse than no composition at all.
_BREAKDOWN_SQL = """
with recursive cat(code) as (
    select code from (values {categories}) as h(code)
    union all
    select c.code from chart_of_accounts c join cat on c.parent_code = cat.code
),
fn(code) as (
    select code from chart_of_accounts where code = '6603'
    union all
    select c.code from chart_of_accounts c join fn on c.parent_code = fn.code
),
scoped_cc as (
    select cc.id
      from cost_centers cc
      left join departments dp on dp.id = cc.department_id
     where cc.is_active
       and (:scope_kind = 'company'
            or (:scope_kind = 'centre' and split_part(cc.code, '-', 1) = :scope_key)
            or (:scope_kind = 'department' and dp.code = :scope_key)
            or (:scope_kind = 'cost_centre' and cc.code = :scope_key))
),
lines as (
    select l.cost_center_id,
           cast(substr(v.fiscal_period, 6, 2) as int) as m,
           l.local_debit as amt,
           coalesce(dim.value_text, ba.code, '') as nc_code,
           coalesce(ba_acct.id, ba.id) as budget_account_id
      from journal_vouchers v
      join journal_voucher_lines l on l.jv_id = v.id
      join cat on cat.code = l.account_code
      join scoped_cc on scoped_cc.id = l.cost_center_id
      left join budget_accounts ba on ba.id = l.income_expense_item_id
      left join budget_accounts ba_acct
             on ba_acct.code = l.account_code and l.account_code in (select code from fn)
      left join jv_line_dimensions dim
             on dim.jv_line_id = l.id and dim.dim_code = 'income_expense_item'
     where v.status = 'posted' and v.fiscal_period like :year_like
),
actual as (
    select budget_account_id, cost_center_id,
           coalesce(sum(amt) filter (where m between :month_from and :month_to), 0) as period,
           coalesce(sum(amt) filter (where m <= :month_to), 0) as ytd
      from lines
     where budget_account_id is not null
       and not (select exists (select 1 from unnest(cast(:excluded as text[])) p
                                where nc_code like p || '%'))
     group by 1, 2
),
plan as (
    select pl.account_id as budget_account_id, p.cost_center_id,
           coalesce(sum(pl.amount), 0) as full_year,
           coalesce(sum(pl.amount) filter (where pl.month between :month_from and :month_to), 0) as period
      from budget_plans p
      join budget_plan_lines pl on pl.plan_id = p.id
      join budget_accounts ba on ba.id = pl.account_id
      join scoped_cc on scoped_cc.id = p.cost_center_id
     where p.fiscal_year = :fiscal_year
       and p.status = 'approved' and p.is_current
       and not (ba.code like any(cast(:excluded_like as text[])))
     group by 1, 2
)
select ba.id, ba.code, ba.name, cc.id, cc.code, cc.name,
       coalesce(plan.full_year, 0), coalesce(plan.period, 0),
       coalesce(actual.period, 0), coalesce(actual.ytd, 0)
  from actual
  full outer join plan
    on plan.budget_account_id = actual.budget_account_id
   and plan.cost_center_id = actual.cost_center_id
  join budget_accounts ba
    on ba.id = coalesce(actual.budget_account_id, plan.budget_account_id)
  join cost_centers cc
    on cc.id = coalesce(actual.cost_center_id, plan.cost_center_id)
 -- A row that is zero on both sides is noise: every cost centre carries a plan
 -- line for every account in the catalog, most of them 0.00.
 where coalesce(plan.full_year, 0) <> 0 or coalesce(actual.ytd, 0) <> 0
    or coalesce(actual.period, 0) <> 0 or coalesce(plan.period, 0) <> 0
 order by ba.code, cc.code
"""


async def breakdown(db, *, fiscal_year: int, month_from: int, month_to: int,
                    scope_kind: str = "company", scope_key: str = "") -> dict[str, Any]:
    """What the selected roll-up figure is made of: budget account × cost centre.

    `scope_kind` is company / centre / department / cost_centre, matching what
    the reader clicked above; `scope_key` is the expense-centre prefix, the
    department code, or the cost-centre code."""
    params = {
        "fiscal_year": fiscal_year, "year_like": f"{fiscal_year}-%",
        "month_from": month_from, "month_to": month_to,
        "scope_kind": scope_kind, "scope_key": scope_key,
        "excluded": list(EXCLUDED_IO_PREFIXES),
        "excluded_like": [f"{p}%" for p in EXCLUDED_IO_PREFIXES],
    }
    rows = (await db.execute(
        text(_BREAKDOWN_SQL.format(categories=_CATEGORIES)), params)).all()

    accounts: dict[str, dict] = {}
    for (ba_id, ba_code, ba_name, cc_id, cc_code, cc_name,
         plan_fy, plan_period, act_period, act_ytd) in rows:
        amounts = (plan_fy, plan_period, act_period, act_ytd)
        acct = accounts.setdefault(str(ba_id), {
            "budget_account_id": str(ba_id), "code": ba_code, "name": ba_name,
            "children": [],
        })
        acct["children"].append({
            "cost_center_id": str(cc_id), "cost_center_code": cc_code,
            "cost_center_name": cc_name,
            **_metrics(*(Decimal(a) for a in amounts)),
        })
        _accumulate(acct, amounts)

    out = []
    for acct in accounts.values():
        children = acct.pop("children")
        out.append({
            **{k: v for k, v in acct.items() if k not in ("fy", "pp", "ap", "ay")},
            **_metrics(acct.get("fy", _ZERO), acct.get("pp", _ZERO),
                       acct.get("ap", _ZERO), acct.get("ay", _ZERO)),
            # One cost centre under an account is not a breakdown, it is the
            # same row twice — the page hides the expander in that case.
            "children": children,
        })
    # By code, not by amount. Finance reads this against a chart of accounts and
    # across periods; a list that reorders itself whenever the numbers move
    # cannot be scanned twice the same way (user, 2026-09-18).
    out.sort(key=lambda a: a["code"] or "")

    # The total of what is listed, from the same numbers the rows are built
    # from. Returned rather than summed in the page so it cannot drift from the
    # rows by a rounding step — and so a reader can check it against the
    # roll-up row they clicked, which is the whole claim this panel makes.
    totals: dict = {}
    for acct in out:
        _accumulate(totals, (acct["plan_full_year"], acct["plan_period"],
                             acct["actual_period"], acct["actual_ytd"]))
    return {
        "fiscal_year": fiscal_year, "month_from": month_from, "month_to": month_to,
        "scope_kind": scope_kind, "scope_key": scope_key,
        "accounts": out,
        "totals": _metrics(totals.get("fy", _ZERO), totals.get("pp", _ZERO),
                           totals.get("ap", _ZERO), totals.get("ay", _ZERO)),
    }


async def rollup(db, *, fiscal_year: int, month_from: int, month_to: int) -> dict[str, Any]:
    """Company / expense centre / department / cost centre, plus what reaches
    none of them."""
    params = {
        "fiscal_year": fiscal_year,
        "year_like": f"{fiscal_year}-%",
        "month_from": month_from,
        "month_to": month_to,
        "excluded": list(EXCLUDED_IO_PREFIXES),
        "excluded_like": [f"{p}%" for p in EXCLUDED_IO_PREFIXES],
    }
    rows = (await db.execute(
        text(_SQL.format(categories=_CATEGORIES)), params)).all()

    company: dict = {}
    centres: dict[str, dict] = {}
    departments: dict[str, dict] = {}
    for (cc_id, cc_code, cc_name, dept_code, dept_name,
         plan_fy, plan_period, act_period, act_ytd) in rows:
        amounts = (plan_fy, plan_period, act_period, act_ytd)
        leaf = {
            "cost_center_id": str(cc_id), "cost_center_code": cc_code,
            "cost_center_name": cc_name,
            "department_code": dept_code, "department_name": dept_name,
            "expense_centre": (cc_code or "").split("-")[0],
            **_metrics(*(Decimal(a) for a in amounts)),
        }
        _accumulate(company, amounts)

        centre_code = leaf["expense_centre"]
        centre = centres.setdefault(centre_code, {"children": []})
        centre["children"].append(leaf)
        _accumulate(centre, amounts)

        # A cost centre with no department would vanish from the department
        # tree while still counting in the company total — surface it instead.
        key = dept_code or "__none__"
        dept = departments.setdefault(key, {"name": dept_name, "children": []})
        dept["children"].append(leaf)
        _accumulate(dept, amounts)

    policy_rows = (await db.execute(
        text(_POLICY_SQL.format(categories=_CATEGORIES)), params)).all()
    unallocated_period = unallocated_ytd = _ZERO
    breakdown = []
    for bucket, period, ytd in policy_rows:
        unallocated_period += Decimal(period)
        unallocated_ytd += Decimal(ytd)
        breakdown.append({
            "key": bucket,
            "label": ("Not placed in any cost centre" if bucket == "__unplaced__"
                      else EXCLUDED_IO_LABELS.get(bucket, bucket)),
            "actual_period": _s(period), "actual_ytd": _s(ytd),
        })
    breakdown.sort(key=lambda b: Decimal(b["actual_ytd"]), reverse=True)

    def node(code: str, label: str, agg: dict, extra: dict) -> dict:
        return {"code": code, "label": label,
                **_metrics(agg.get("fy", _ZERO), agg.get("pp", _ZERO),
                           agg.get("ap", _ZERO), agg.get("ay", _ZERO)),
                **extra}

    centre_nodes = [
        node(c, EXPENSE_CENTRE_LABELS.get(c, c), agg,
             {"children": sorted(agg["children"], key=lambda x: x["cost_center_code"])})
        for c, agg in sorted(centres.items(),
                             key=lambda kv: -Decimal(kv[1].get("fy", _ZERO)))
    ]
    dept_nodes = [
        node(k if k != "__none__" else "", agg["name"] or "(no department)", agg,
             {"children": sorted(agg["children"], key=lambda x: x["cost_center_code"])})
        for k, agg in sorted(departments.items(),
                             key=lambda kv: -Decimal(kv[1].get("fy", _ZERO)))
    ]

    company_metrics = _metrics(company.get("fy", _ZERO), company.get("pp", _ZERO),
                               company.get("ap", _ZERO), company.get("ay", _ZERO))
    return {
        "fiscal_year": fiscal_year,
        "month_from": month_from, "month_to": month_to,
        "company": company_metrics,
        "by_expense_centre": centre_nodes,
        "by_department": dept_nodes,
        "unallocated": {
            "actual_period": _s(unallocated_period),
            "actual_ytd": _s(unallocated_ytd),
            "breakdown": breakdown,
        },
        # Everything posted in the window, however it was classified. A report
        # whose parts do not add to the whole is not a report.
        "reconciliation": {
            "placed_actual_period": company_metrics["actual_period"],
            "unallocated_actual_period": _s(unallocated_period),
            "total_actual_period": _s(
                Decimal(company_metrics["actual_period"]) + unallocated_period),
        },
    }
