"""What the plan and the doc-side actual on the Budget Dashboard are made of.

The companion to finance-api's budget_actual_lineage: that one explains the NC
line of each cell, this one explains the two above it. Both exist because the
question "how did this number get here" was being answered with "ask Finance",
while every part of the answer was sitting in code nothing could read out loud.

Derived, not described:

  plan        the filter the monthly summary applies — approved and current —
              stated once, here, and asserted against the query in
              tests/test_lineage.py.
  actual      `models.ledger.ACTUAL_OPS`, the tuple the aggregation uses. A new
              operation, or one moved between buckets, moves this answer with it.
  op_mix      counted live. Which operations exist is the difference between
              "actual means invoices posted against budget" and what is
              currently true, which is that every row came from the opening
              import. Stating the design without the count would be accurate and
              still leave someone reading a number as something it is not.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import ACTUAL_OPS, COMMIT_ADDS, COMMIT_RELEASES, BudgetLedger

_OP_MEANS = {
    "commit": "a purchase request or order set money aside",
    "release": "money set aside was given back",
    "actualize": "an invoice turned a commitment into a real cost",
    "book_expense": "an expense claim booked a cost with nothing committed first",
    "opening": "actuals imported for the part of the year before this ledger "
               "existed — a figure finance loaded, not a document",
}


async def _operation_mix(db: AsyncSession) -> list[dict]:
    """How many entries of each operation exist. Counts only — no amounts, so
    this says what the pipeline is doing without disclosing anyone's spending."""
    rows = (await db.execute(
        select(BudgetLedger.operation, func.count())
        .group_by(BudgetLedger.operation))).all()
    return sorted(
        ({"operation": op, "entries": int(n), "means": _OP_MEANS.get(op, op)}
         for op, n in rows),
        key=lambda r: -r["entries"])


async def describe(db: AsyncSession) -> dict:
    """The two document-side figures on the dashboard, and what feeds them."""
    mix = await _operation_mix(db)
    only = [r for r in mix if r["entries"]]
    return {
        "figures": [
            {
                "figure": "plan",
                "label": "plan",
                "what_it_counts": (
                    "The monthly amounts on the budget plan for that cost "
                    "centre and year."
                ),
                "which_plan": (
                    "Only a plan that is approved AND marked current. A draft, "
                    "a rejected plan or a superseded revision contributes "
                    "nothing, so a cost centre whose plan has not been approved "
                    "shows no plan at all rather than a provisional one."
                ),
                "no_cost_centre_selected": (
                    "With no cost centre chosen the figures are added across "
                    "every cost centre that has a current approved plan for "
                    "that year."
                ),
            },
            {
                "figure": "actual_docs",
                "label": "actual (docs)",
                "what_it_counts": (
                    "Movements recorded in the budget ledger by the documents "
                    "themselves, in the month the movement was recorded for."
                ),
                "operations_that_count": [
                    {"operation": op, "means": _OP_MEANS.get(op, op)}
                    for op in ACTUAL_OPS
                ],
                "committed_is_separate": (
                    "Committed is the other half of the same ledger: "
                    f"{'/'.join(COMMIT_ADDS)} adds to it and "
                    f"{'/'.join(COMMIT_RELEASES)} gives it back. It is money "
                    "promised, not money spent, and it is not part of actual."
                ),
                "what_is_in_it_now": mix,
                "read_it_this_way": (
                    "Only these operations are present: "
                    + ", ".join(f"{r['operation']} ({r['entries']})" for r in only)
                    + ". Anything the list does not contain is not being "
                    "recorded yet, and a figure made only of `opening` is what "
                    "finance imported, not what documents have consumed."
                    if only else
                    "The ledger is empty, so this line is zero everywhere — "
                    "which means nothing has been recorded, not that nothing "
                    "was spent."
                ),
            },
        ],
    }
