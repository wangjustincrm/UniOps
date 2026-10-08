"""JV dimension validation — the rules that decide whether a posted NC voucher
line can actually reach a budget cell.

The Budget Dashboard's NC-actual line is not a sum over vouchers; it is a sum
over `journal_voucher_lines` that carry BOTH a resolved `cost_center_id` and a
resolved `income_expense_item_id` (crud/account_balance.nc_actuals_monthly joins
budget_accounts INNER). A line that fails either resolution is not wrong-looking
anywhere — it is silently absent, and the only visible symptom is a cost center
whose actual is 0 or a total that no longer equals the sum of its parts.

Three real cases from 2026 production, all invisible until this module:
  * SELL-0107-S03 (budget 669,700) had zero actual all year — NC never fills a
    cost center on 6601 x dept 0107 and the map row demands the exact 'S03'.
  * 27,212.38 of CRM671105 never reached its budget account: NC's income-expense
    codes carry a 'CRM' prefix and that account is filed as '671105'.
  * 2,775.00 on 510102 with NC cost center 'QA' (the map knows only 'Q01') landed
    in no cost center at all — visible in "All cost centers", nowhere else.

One SQL body serves both callers: the API (async SQLAlchemy) and the post-sync
task raiser (psycopg2, inside the sync's own transaction). Keeping it single
means a rule can never mean one thing on the page and another in the inbox.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

# Predreal account -> the cost-center code prefix its lines must carry. Same
# five accounts (and the same category codes) as crud.account_balance's
# BUDGET_ACTUAL_ACCOUNTS — imported from there so the two can never drift.
from app.crud.account_balance import BUDGET_ACTUAL_ACCOUNTS, EXCLUDED_IO_PREFIXES

#: Rule codes, in the order the page shows them. Ordered by how much they cost:
#: an unresolved dimension silently drops money out of the dashboard; a mismatch
#: still lands somewhere, just possibly in the wrong bucket.
RULES: tuple[str, ...] = (
    "cost_center_no_map_for_department",
    "cost_center_code_unmatched",
    "income_expense_unresolved",
    "income_expense_missing",
    "account_not_in_catalog",
    "category_mismatch",
    "department_mismatch",
    "cc_map_drift",
)

RULE_LABELS: dict[str, str] = {
    # The two halves of "no cost center", split because they need DIFFERENT
    # people: the first is a finance decision (nobody has said where this
    # account x department belongs), the second is a missing row in a mapping
    # whose destination is already agreed.
    "cost_center_no_map_for_department":
        "No cost center mapping for this account x department — needs a decision",
    "cost_center_code_unmatched":
        "NC cost center code is not in the mapping for this account x department",
    "income_expense_unresolved":
        "Income-expense code not in the budget account catalog",
    "income_expense_missing":
        "No income-expense item on the line at all",
    "account_not_in_catalog":
        "Financial-expense account has no budget account with the same code",
    "category_mismatch":
        "Account category and cost center disagree",
    "department_mismatch":
        "Department and cost center disagree",
    "cc_map_drift":
        "Cost center differs from what the current mapping resolves to",
    # (rule text only — the exclusion for unreplayable lines is in the SQL)
}

#: Rules whose lines are MISSING from the Budget Dashboard entirely (as opposed
#: to landing in a questionable bucket). Their amounts are what "the parts do
#: not add up to the total" is made of.
DROPPED_RULES: frozenset[str] = frozenset({
    "cost_center_no_map_for_department", "cost_center_code_unmatched",
    "income_expense_unresolved", "income_expense_missing",
    "account_not_in_catalog",
})

#: Findings a person must rule on before any amount of re-syncing can place the
#: line — there is no mapping row to add until someone decides where it belongs.
#: The page marks these separately so they are not mistaken for a config gap.
NEEDS_DECISION_RULES: frozenset[str] = frozenset({
    "cost_center_no_map_for_department",
})


def _category_prefix_case(column: str) -> str:
    """SQL CASE mapping a predreal account code to its cost-center prefix."""
    whens = " ".join(f"when '{acct}' then '{prefix}'"
                     for acct, prefix in BUDGET_ACTUAL_ACCOUNTS.items())
    return f"case {column} {whens} end"


_CATEGORY_VALUES = ", ".join(f"('{a}')" for a in BUDGET_ACTUAL_ACCOUNTS)

# Matches the NC income-expense code TEXT, not the budget account: these items
# are excluded whether or not the catalog has a row for them, and CRM09912 has
# none — matching on the resolved account would miss every one of its lines.
#
# Passed as a PARAMETER rather than inlined: a literal 'CRM004%' in the SQL body
# is a format specifier to psycopg2 (`cur.execute(sql, params)`), which raises
# "not enough arguments for format string" on the sync path while the async path
# works fine — a break that only shows up in the post-sync task raiser.
_EXCLUDED_IO_PATTERNS = [f"{p}%" for p in EXCLUDED_IO_PREFIXES]

# ── The query ────────────────────────────────────────────────────────────────
#
# `cat` walks chart_of_accounts down from the five predreal headers, carrying the
# header code as the category — never inferred from a code prefix, because a
# 660303 can sit under 6603 OR 6601 (same reasoning as nc_sync.make_category_of).
#
# `expected_cc` replays cc_map_import.resolve_uniops_cc's three-step fallback in
# SQL: (account, dept, nc_cc) -> (account, dept, ALL) -> (account, ALL, ALL).
# It is what a re-sync WOULD write today, which is what makes `cc_map_drift`
# usable as a preview of a mapping change before running one.
_SQL = f"""
with recursive cat(code, category) as (
    -- Seeded from the constant, NOT from chart_of_accounts: if a header row were
    -- ever missing from the COA mirror, seeding off the table would drop that
    -- whole category out of validation silently — exactly the class of failure
    -- this module exists to catch.
    select code, code from (values {_CATEGORY_VALUES}) as h(code)
    union all
    select c.code, cat.category
      from chart_of_accounts c join cat on c.parent_code = cat.code
),
base as (
    select v.id            as jv_id,
           v.jv_number     as jv_number,
           v.fiscal_period as fiscal_period,
           v.voucher_date  as voucher_date,
           v.summary       as voucher_summary,
           l.id            as line_id,
           l.line_no       as line_no,
           l.account_code  as account_code,
           cat.category    as category,
           l.summary       as line_summary,
           l.local_debit   as local_debit,
           l.local_credit  as local_credit,
           l.cost_center_id, cc.code as cc_code,
           l.nc_cc_code, l.department_id, dp.code as dept_code,
           l.income_expense_item_id, ba.code as ba_code,
           ba_acct.code    as ba_by_account_code,
           dim.value_text  as io_text,
           coalesce(
             (select m.uniops_cc_code from budget_actual_cc_map m
               where m.account_code = cat.category
                 and m.dept_code   = coalesce(dp.code, '')
                 and m.nc_cc_code  = coalesce(l.nc_cc_code, '')),
             (select m.uniops_cc_code from budget_actual_cc_map m
               where m.account_code = cat.category
                 and m.dept_code   = coalesce(dp.code, '')
                 and m.nc_cc_code  = 'ALL'),
             (select m.uniops_cc_code from budget_actual_cc_map m
               where m.account_code = cat.category
                 and m.dept_code   = 'ALL' and m.nc_cc_code = 'ALL')
           ) as expected_cc
      from journal_vouchers v
      join journal_voucher_lines l on l.jv_id = v.id
      join cat on cat.code = l.account_code
      left join cost_centers cc on cc.id = l.cost_center_id
      left join departments dp on dp.id = l.department_id
      left join budget_accounts ba on ba.id = l.income_expense_item_id
      -- 6603 only: NC books financial expenses by ACCOUNT, with no
      -- income-expense item, and each account has a budget account of the same
      -- code (660301 Interest income, 660303 Bank Charge, ...).
      left join budget_accounts ba_acct
             on ba_acct.code = l.account_code and cat.category = '6603'
      left join jv_line_dimensions dim
             on dim.jv_line_id = l.id and dim.dim_code = 'income_expense_item'
     where v.status = 'posted'
       and v.fiscal_period >= %(period_from)s
       and v.fiscal_period <= %(period_to)s
       -- Payroll / depreciation / shut-down loss are out of the dashboard by
       -- decision, so they are out of its validation too: a line is not held to
       -- rules about a placement it is never given (user, 2026-09-17). Matched
       -- on the NC code TEXT, not the resolved account — CRM09912 has no
       -- catalog row, so an account-side match would miss every line of it.
       and not coalesce(dim.value_text like any(%(excluded_io)s), false)
),
flagged as (
    select b.*, r.rule
      from base b
      cross join lateral (values
        -- Split of "no cost center": does the map know this account x
        -- department AT ALL? If not, no row can be added until finance says
        -- where it belongs (6602 x dept 0104 / 0106 in production). If it does,
        -- the NC cost-center code simply is not one of the ones listed.
        ('cost_center_no_map_for_department',
         b.cost_center_id is null
         and not exists (select 1 from budget_actual_cc_map m
                          where m.account_code = b.category
                            and m.dept_code in (coalesce(b.dept_code, ''), 'ALL'))),
        ('cost_center_code_unmatched',
         b.cost_center_id is null
         and exists (select 1 from budget_actual_cc_map m
                      where m.account_code = b.category
                        and m.dept_code in (coalesce(b.dept_code, ''), 'ALL'))),
        -- The income-expense pair applies only where NC actually books by
        -- income-expense item. 6603 is checked by account instead, below.
        ('income_expense_unresolved',
         b.category <> '6603'
         and b.income_expense_item_id is null and b.io_text is not null),
        ('income_expense_missing',
         b.category <> '6603'
         and b.income_expense_item_id is null and b.io_text is null),
        ('account_not_in_catalog',
         b.category = '6603' and b.ba_by_account_code is null),
        ('category_mismatch',
         b.cc_code is not null
         and split_part(b.cc_code, '-', 1) is distinct from {_category_prefix_case('b.category')}),
        ('department_mismatch',
         b.cc_code is not null and b.dept_code is not null
         and split_part(b.cc_code, '-', 2) ~ '^[0-9]+$'
         and split_part(b.cc_code, '-', 2) <> b.dept_code
         and not exists (select 1 from budget_actual_cc_map m
                          where m.account_code = b.category
                            and m.uniops_cc_code = b.cc_code
                            and m.dept_code in (b.dept_code, 'ALL'))),
        -- Not comparable when the line carries no department: the mirror stores
        -- the RESOLVED department_id, and NC department codes that EPMS has no
        -- row for (0108, which the map does place — 6602/0108 -> GA-0100)
        -- resolve to NULL. Recomputing from a NULL department then "expects"
        -- no cost center and reports drift on a line the sync placed correctly.
        -- Only a line whose department we can actually replay is judged here.
        ('cc_map_drift',
         b.expected_cc is distinct from b.cc_code
         and not (b.dept_code is null and b.cc_code is not null
                  and b.expected_cc is null))
      ) as r(rule, hit)
     where r.hit
)
"""

_SUMMARY_SQL = _SQL + """
select rule,
       count(*)                       as lines,
       count(distinct jv_id)          as vouchers,
       coalesce(sum(local_debit), 0)  as debit,
       coalesce(sum(local_credit), 0) as credit
  from flagged
 group by rule
"""

_ROWS_SQL = _SQL + """
select rule, jv_id, jv_number, fiscal_period, voucher_date, voucher_summary,
       line_id, line_no, account_code, category, line_summary,
       local_debit, local_credit,
       cc_code, nc_cc_code, dept_code, expected_cc,
       ba_code, io_text
  from flagged
 where (cast(%(rule)s as text) is null or rule = cast(%(rule)s as text))
 order by fiscal_period, jv_number, line_no, rule
 limit %(limit)s offset %(offset)s
"""

_ROWS_COUNT_SQL = _SQL + """
select count(*) from flagged where (cast(%(rule)s as text) is null or rule = cast(%(rule)s as text))
"""


def _params(period_from: str, period_to: str, **extra) -> dict[str, Any]:
    return {"period_from": period_from, "period_to": period_to,
            "excluded_io": _EXCLUDED_IO_PATTERNS, **extra}


def _to_named(sql: str) -> str:
    """psycopg2 `%(name)s` -> SQLAlchemy `:name`. The SQL is authored in
    psycopg2 style because the sync path (which runs on a raw cursor) is the one
    that must not gain a dependency; the API side converts."""
    import re
    return re.sub(r"%\((\w+)\)s", r":\1", sql)


def _d(v) -> str:
    return str(Decimal(v or 0).quantize(Decimal("0.01")))


# ── async (API) ──────────────────────────────────────────────────────────────

async def summary(db, period_from: str, period_to: str) -> list[dict[str, Any]]:
    """Per-rule counts + amounts for the period range, in RULES order."""
    from sqlalchemy import text
    rows = (await db.execute(text(_to_named(_SUMMARY_SQL)),
                             _params(period_from, period_to))).all()
    by_rule = {r[0]: r for r in rows}
    out = []
    for rule in RULES:
        r = by_rule.get(rule)
        out.append({
            "rule": rule,
            "label": RULE_LABELS[rule],
            "drops_from_dashboard": rule in DROPPED_RULES,
            "needs_decision": rule in NEEDS_DECISION_RULES,
            "lines": int(r[1]) if r else 0,
            "vouchers": int(r[2]) if r else 0,
            "debit": _d(r[3]) if r else "0.00",
            "credit": _d(r[4]) if r else "0.00",
        })
    return out


async def rows(db, period_from: str, period_to: str, *, rule: str | None = None,
               limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """One page of flagged lines (a line appears once per rule it breaks)."""
    from sqlalchemy import text
    p = _params(period_from, period_to, rule=rule, limit=limit, offset=offset)
    total = (await db.execute(text(_to_named(_ROWS_COUNT_SQL)), p)).scalar_one()
    res = (await db.execute(text(_to_named(_ROWS_SQL)), p)).mappings().all()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "rows": [{
            "rule": r["rule"],
            "label": RULE_LABELS.get(r["rule"], r["rule"]),
            "jv_id": str(r["jv_id"]),
            "jv_number": r["jv_number"],
            "fiscal_period": r["fiscal_period"],
            "voucher_date": r["voucher_date"].isoformat() if r["voucher_date"] else None,
            "voucher_summary": r["voucher_summary"],
            "line_no": r["line_no"],
            "account_code": r["account_code"],
            "category": r["category"],
            "line_summary": r["line_summary"],
            "debit": _d(r["local_debit"]),
            "credit": _d(r["local_credit"]),
            "cost_center_code": r["cc_code"],
            "nc_cost_center_code": r["nc_cc_code"],
            "department_code": r["dept_code"],
            "expected_cost_center_code": r["expected_cc"],
            "budget_account_code": r["ba_code"],
            "income_expense_text": r["io_text"],
        } for r in res],
    }


# ── sync (post-sync task raiser) ─────────────────────────────────────────────

def summary_sync(cur, period_from: str, period_to: str) -> dict[str, dict]:
    """Same summary over a raw psycopg2 cursor -> {rule: {...}}. Used inside the
    NC sync's own transaction, so what the inbox reports is exactly what the run
    just wrote."""
    cur.execute(_SUMMARY_SQL, _params(period_from, period_to))
    out = {r[0]: {"lines": int(r[1]), "vouchers": int(r[2]),
                  "debit": Decimal(r[3] or 0), "credit": Decimal(r[4] or 0)}
           for r in cur.fetchall()}
    return out
