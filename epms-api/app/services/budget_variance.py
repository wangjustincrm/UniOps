"""Budget against what was actually spent, per cost centre, with the variance.

The question a controlled query cannot reach. Someone asked for "各成本中心年度
总预算和到 2026 年 8 月为止的总实际值，给出差异额和差异百分比" and the assistant
returned the plan alone, then — when told to go and get the rest — returned the
plan again. Nothing was broken: the query layer answers from ONE entity and the
model is not allowed to do arithmetic, so a figure that needs two tables and a
subtraction has no path through it at all.

So the subtraction happens here, in code, the same way "how many can we make"
does. The model chooses the year, the month and the cut; it never computes a
number and never sees a row it could add up wrongly.

Where each half comes from, and why not from somewhere closer:

  plan    budget_plans + budget_plan_lines, read straight from the shared
          database through the ontology's own declarations — so the rows behind
          this answer are the rows a query would have returned, and the filter
          (approved AND current) is the same one the dashboard applies.

  actual  finance-api. Not re-implemented here: the NC-posted figure is five
          account subtrees, posted-only, gross debit, two budget-account keying
          rules and three excluded item families, and a second copy of that
          would be a second answer to "what has this cost centre spent".

`dropped` travels with the answer because this comparison is exactly where a
silently incomplete actual does damage: an under-reported actual reads as money
saved. Finance-api counts what its own window leaves out and this passes it on.
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ontology import _own_department_cost_centres, get_entity, may_view
from app.services import finance_client

_ZERO = Decimal("0")

# The gate is the budget entities' own. Someone who cannot query a budget plan
# must not get the same numbers back through a route that adds them up.
_PLAN_ENTITY = "budget_plan"


def _s(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01")))


async def _visible_cost_centres(db: AsyncSession, scope: dict) -> list | None:
    """The cost centres this caller may see, or None for the whole company.

    Reuses the ontology's own scope helper rather than a second opinion about
    departments, so a computed total cannot be wider than a query would have
    been. An empty list is a real answer — 20 of 112 users have no department —
    and means the caller sees nothing.
    """
    if (scope.get("perms") or {}).get("finance.budget.view_all"):
        return None
    rows = (await db.execute(await _own_department_cost_centres(scope))).scalars().all()
    return list(rows)


async def _plan_by_cost_centre(db: AsyncSession, fiscal_year: int,
                               through_month: int, cc_ids: list | None) -> dict:
    """Planned amount per cost centre: annual, and the part up to `through_month`.

    Both, because the comparison people mean is not always the same one. "How
    are we doing against the year" wants the annual figure; "are we on track"
    wants the same months on both sides. Reporting only one of them invites the
    other to be read off it.
    """
    plans = get_entity("budget_plan").model
    lines = get_entity("budget_plan_line").model
    ytd = sa.case((lines.month <= through_month, lines.amount), else_=0)
    q = (sa.select(plans.cost_center_id,
                   sa.func.coalesce(sa.func.sum(lines.amount), 0),
                   sa.func.coalesce(sa.func.sum(ytd), 0))
         .join(plans, lines.plan_id == plans.id)
         .where(plans.fiscal_year == fiscal_year,
                plans.status == "approved",
                plans.is_current.is_(True))
         .group_by(plans.cost_center_id))
    if cc_ids is not None:
        q = q.where(plans.cost_center_id.in_(cc_ids))
    return {str(cc): (Decimal(str(total)), Decimal(str(part)))
            for cc, total, part in (await db.execute(q)).all()}


async def _cost_centre_names(db: AsyncSession, ids: set) -> dict:
    if not ids:
        return {}
    cc = get_entity("cost_center").model
    rows = (await db.execute(
        sa.select(cc.id, cc.code, cc.name).where(cc.id.in_(list(ids))))).all()
    return {str(i): (code, name) for i, code, name in rows}


def _variance(plan: Decimal, actual: Decimal) -> tuple[Decimal, str | None]:
    """Plan minus actual, and that as a share of the plan.

    Positive is under budget. The percentage is deliberately absent rather than
    zero when there is no plan: "100% over" against a budget of nothing is a
    number with no meaning, and it sorts to the top of exactly the list someone
    is scanning for their worst overspend.
    """
    var = plan - actual
    if plan == _ZERO:
        return var, None
    return var, str((var / plan * 100).quantize(Decimal("0.1")))


async def build(db: AsyncSession, scope: dict, *, fiscal_year: int,
                through_month: int, token: str | None) -> dict:
    """Plan, actual and variance per cost centre for one year."""
    entity = get_entity(_PLAN_ENTITY)
    if entity is None or not may_view(entity, scope.get("perms") or {}):
        return {"allowed": False,
                "why": "Seeing budget figures needs budget or finance access."}

    cc_ids = await _visible_cost_centres(db, scope)
    if cc_ids is not None and not cc_ids:
        return {"allowed": True, "fiscal_year": fiscal_year,
                "through_month": through_month, "rows": [],
                "nothing_visible": (
                    "This person's account has no department on it, so no cost "
                    "centre is in scope. That is the reason for the empty "
                    "answer — not that there are no budgets.")}

    plan = await _plan_by_cost_centre(db, fiscal_year, through_month, cc_ids)
    actual_side = await finance_client.nc_actuals_by_cost_center(
        bearer_token=token, fiscal_year=fiscal_year, through_month=through_month)
    if actual_side is None:
        return {"allowed": True, "fiscal_year": fiscal_year,
                "through_month": through_month, "rows": [],
                "actual_unavailable": (
                    "finance-api could not be reached, so there is no actual to "
                    "compare the plan with. Do not present the plan alone as an "
                    "answer to a question about variance.")}

    actual = {r["cost_center_id"]: (Decimal(r["actual"]), r["cost_center_code"],
                                    r["cost_center_name"])
              for r in actual_side.get("cost_centers", [])}
    if cc_ids is not None:
        allowed = {str(c) for c in cc_ids}
        actual = {k: v for k, v in actual.items() if k in allowed}

    names = await _cost_centre_names(db, set(plan) | set(actual))
    rows = []
    for cc_id in set(plan) | set(actual):
        annual, ytd_plan = plan.get(cc_id, (_ZERO, _ZERO))
        spent, code, name = actual.get(cc_id, (_ZERO, None, None))
        code = code or (names.get(cc_id) or (None, None))[0]
        name = name or (names.get(cc_id) or (None, None))[1]
        var, pct = _variance(annual, spent)
        ytd_var, ytd_pct = _variance(ytd_plan, spent)
        rows.append({
            "cost_center_code": code, "cost_center_name": name,
            "annual_plan": _s(annual),
            "plan_to_date": _s(ytd_plan),
            "actual_to_date": _s(spent),
            "variance_vs_annual": _s(var),
            "variance_pct_vs_annual": pct,
            "variance_vs_plan_to_date": _s(ytd_var),
            "variance_pct_vs_plan_to_date": ytd_pct,
            # Said per row rather than left to be noticed: a cost centre with
            # spending and no approved plan is not "100% under budget".
            "no_approved_plan": annual == _ZERO,
        })
    rows.sort(key=lambda r: r["cost_center_code"] or "￿")

    total_plan = sum((Decimal(r["annual_plan"]) for r in rows), _ZERO)
    total_ytd_plan = sum((Decimal(r["plan_to_date"]) for r in rows), _ZERO)
    total_actual = sum((Decimal(r["actual_to_date"]) for r in rows), _ZERO)
    t_var, t_pct = _variance(total_plan, total_actual)
    return {
        "allowed": True,
        "fiscal_year": fiscal_year,
        "through_month": through_month,
        "rows": rows,
        "totals": {
            "annual_plan": _s(total_plan),
            "plan_to_date": _s(total_ytd_plan),
            "actual_to_date": _s(total_actual),
            "variance_vs_annual": _s(t_var),
            "variance_pct_vs_annual": t_pct,
        },
        "scope_was": ("every cost centre" if cc_ids is None
                      else "this person's own department's cost centres"),
        "not_in_the_actual": actual_side.get("dropped"),
        "not_in_the_actual_means": actual_side.get("dropped_means"),
        "how_it_was_worked_out": (
            "Plan is the approved, current budget for the year. Actual is NC "
            "posted spending — the same basis as the Budget Dashboard — for "
            f"months 1 to {through_month}. Variance is plan minus actual, so a "
            "positive number is under budget. Both comparisons are given: "
            "against the whole year's plan, and against the same months of it."
        ),
    }
