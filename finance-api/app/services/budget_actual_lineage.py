"""What the NC-posted actual on the Budget Plan vs Actual grid is made of.

A manager looking at a cell asks two questions, in this order: where did that
number come from, and why is it against MY cost centre. Both answers are code
that lives here — the five expense categories, the two exclusions, posted-only,
gross debit, and the order the cost-centre map is consulted in — and neither was
reachable by anything that talks to people. The assistant answered "that is the
system's internal data flow, ask Finance".

So this module states the rule, and states it from the same objects the sync and
the grid run on rather than in words beside them:

  categories        `crud.account_balance.BUDGET_ACTUAL_ACCOUNTS`, the dict the
                    grid iterates.
  excluded          `EXCLUDED_IO_LABELS`, the prefixes the grid
                    lifts out of the per-cost-centre rows.
  budget account    `_BY_ACCOUNT_CODE_CATEGORY`, the one category keyed by
                    account code rather than by income-expense item.
  cost-centre order NOT a description of `resolve_uniops_cc` — the probe below
                    runs it and reports which of three planted rules it chose.
                    Reordering the resolver changes this answer.

What it deliberately does not do is read the map itself. Those twenty rows are
finance's to edit, they change without a deploy, and whoever is answering can
read them straight out of budget_actual_cc_map.
"""
from app.crud.account_balance import (
    _BY_ACCOUNT_CODE_CATEGORY,
    BUDGET_ACTUAL_ACCOUNTS,
    EXCLUDED_IO_LABELS,
)
from app.services.cc_map_import import resolve_uniops_cc

# Codes that exist in no NC catalogue, so a hit can only have come from the
# planted rule of that shape.
_PROBE_ACCOUNT = "5101"
_PROBE_DEPT = "ZZ99"
_PROBE_CC = "ZZ9"

_PROBE_RULES = [
    {"account_code": _PROBE_ACCOUNT, "dept_code": _PROBE_DEPT,
     "nc_cc_code": _PROBE_CC, "uniops_cc_code": "exact"},
    {"account_code": _PROBE_ACCOUNT, "dept_code": _PROBE_DEPT,
     "nc_cc_code": "ALL", "uniops_cc_code": "department_wildcard"},
    {"account_code": _PROBE_ACCOUNT, "dept_code": "ALL",
     "nc_cc_code": "ALL", "uniops_cc_code": "account_wildcard"},
]

_PRECEDENCE_LABELS = {
    "exact": "the rule for this account, this NC department AND this NC cost "
             "centre code",
    "department_wildcard": "the rule for this account and this NC department, "
                           "whatever the cost-centre code (nc_cc_code = ALL)",
    "account_wildcard": "the rule for this account alone (dept_code and "
                        "nc_cc_code both ALL)",
}


def _observed_precedence() -> list[dict]:
    """Which rule wins, found by asking the resolver rather than reading it.

    Each step removes the rule the previous step matched and runs the resolver
    again; what it returns next is, by construction, the next preference. The
    last step offers it nothing and records that the answer is then "no cost
    centre" — which is the case the exceptions panel exists for.
    """
    rules = list(_PROBE_RULES)
    order: list[dict] = []
    while rules:
        hit = resolve_uniops_cc(rules, _PROBE_ACCOUNT, _PROBE_DEPT, _PROBE_CC)
        if hit is None:
            break
        order.append({"rank": len(order) + 1, "matches": _PRECEDENCE_LABELS.get(hit, hit)})
        rules = [r for r in rules if r["uniops_cc_code"] != hit]
    return order


def unmatched_result() -> str | None:
    """What the resolver returns when nothing matches. Asked, not assumed."""
    return resolve_uniops_cc([], _PROBE_ACCOUNT, _PROBE_DEPT, _PROBE_CC)


def describe() -> dict:
    """The rule behind the NC-posted figures, for whoever has to explain it."""
    return {
        "figure": "nc_posted",
        "label": "NC posted",
        "what_it_counts": (
            "Journal voucher lines mirrored from NC, under the five expense "
            "categories, for the month of the voucher's fiscal period."
        ),
        "categories": [
            {"account_code": code, "category": category}
            for code, category in BUDGET_ACTUAL_ACCOUNTS.items()
        ],
        "subtree": (
            "A category includes every account beneath it in the chart of "
            "accounts, followed by parent_code — never by how the code is "
            "spelled. 660303 can sit under 6603 or under 6601."
        ),
        # Which budget line a voucher line lands on — two rules, because NC
        # books the two families differently. Derived from the constant the
        # query itself branches on, so the answer cannot outlive the rule.
        "budget_account_rule": {
            "by_income_expense_item": (
                "For manufacturing overhead, R&D, selling and G&A the line "
                "carries a 收支项目 (income-expense item) and THAT is the budget "
                "line. A line without one reaches no budget cell at all."
            ),
            "by_account_code": (
                f"For {_BY_ACCOUNT_CODE_CATEGORY} financial expenses NC puts no "
                "income-expense item on the line. The ACCOUNT is the budget "
                "line instead — 660301 Interest income, 660303 Bank Charge — "
                "each matched to the budget account of exactly the same code."
            ),
            "by_account_code_applies_to": _BY_ACCOUNT_CODE_CATEGORY,
        },
        "posted_only": (
            "Only vouchers NC has tallied count. An untallied voucher is a "
            "draft in our mirror and colours nothing."
        ),
        "value": (
            "The gross DEBIT of the line in the reporting currency. Expense "
            "accounts net to about nothing once the period is closed out, so a "
            "debit-minus-credit figure would read as zero spending."
        ),
        "month_from": (
            "The voucher's fiscal period, not the day anything was entered."
        ),
        # Derived from the query's own exclusion set, not restated: an item
        # added there must not keep being described as included here.
        "excluded_from_the_dashboard": [
            {"budget_account_prefix": prefix, "what": what}
            for prefix, what in EXCLUDED_IO_LABELS.items()
        ],
        "excluded_why": (
            "Finance tracks payroll and depreciation at category level only, "
            "so they are not spread over cost centres, and shut-down loss — "
            "the stop-production entry that moves a share of manufacturing "
            "overhead into G&A — is not budgeted per cost centre at all. They "
            "are left out of the dashboard's rows and totals entirely; the "
            "finance grid shows payroll and depreciation as separate tie-out "
            "lines. A cost centre's dashboard total is therefore smaller than "
            "its total in the books, by design."
        ),
        "cost_centre_rule": {
            "how": (
                "The cost centre is decided once, when the voucher is mirrored "
                "from NC, from the account together with the NC department and "
                "NC cost-centre code on the line. It is account-aware on "
                "purpose: the same NC department belongs to a different UniOps "
                "cost centre under a different expense account."
            ),
            "rules_live_in": "budget_actual_cc_map",
            "precedence": _observed_precedence(),
            "when_nothing_matches": (
                "The line gets no cost centre at all. That is not a silent "
                "default — the combination is being called an invalid budget "
                "bucket, and the line appears in the exceptions panel of the "
                "finance grid for someone to fix in NC or add to the map."
            ),
            "unmatched_returns": unmatched_result(),
            "other_accounts": (
                "Outside these five categories a line keeps an older, "
                "account-blind mapping by NC code or department. Those lines "
                "are not on this report."
            ),
        },
    }
