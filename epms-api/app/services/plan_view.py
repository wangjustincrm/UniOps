"""Why a production plan says what it says.

Asked why the plan makes 619 of S0102 in the second week of October, the
assistant answered that the system records the plan but not the reasoning. That
is the same mistake it made about approvers: the reasoning IS recorded, spread
across the columns of the line itself. Forecast 4990, opening stock 4371, and
4990 - 4371 = 619. Nothing was missing except something to assemble it.

Two rules this module holds to:

  * the arithmetic is read, never performed. Every number here comes out of the
    row; this module puts them in the order the engine derived them and says
    which is which. Where a line departs from the plain subtraction, a flag on
    the row says why, and the flag is what gets reported.

  * the flag descriptions are the engine's own words, quoted from
    mrp-api/app/services/mps_engine.py where each field is declared. They are
    not paraphrased here, because a paraphrase drifts from the behaviour and
    this is precisely the kind of explanation someone will act on. If the engine
    changes what a flag means, these lines are wrong and should be re-copied.

The net-requirement formula comes from mrp-api's net_requirement.py, which
states it as the definition everything downstream depends on:

    net_requirement[month] = max(0, forecast[month] - opening_stock[month])
"""
import uuid
from decimal import Decimal

import sqlalchemy as sa

# flag -> what it means, from mps_engine.py's declarations. Ordered by how much
# a planner needs to know about it.
FLAG_MEANINGS: dict[str, str] = {
    "capacity_gap": (
        "Did not fit in this week's capacity bucket. The quantity shown is what "
        "could be scheduled here, not the whole requirement."),
    "lead_shortfall": (
        "There was not enough lead time before the demand to make this "
        "normally."),
    "late_production": (
        "Scheduled in a week LATER than the demand month, because neither that "
        "month nor any earlier week could host a whole lot. The goods arrive "
        "after they were needed."),
    "below_min_lot": (
        "Runs BELOW the product's minimum lot size, because weekly capacity "
        "cannot reach the lot in a single week. Capacity wins over the floor — "
        "this is the one sanctioned exception to \"produce 0 or at least a "
        "lot\"."),
    "is_prebuild": (
        "Pulled into a month EARLIER than the one the demand was bucketed "
        "into — stock made in a month it was not planned for, waiting in a "
        "warehouse."),
    "covered_by_carry": (
        "This demand month was already covered by an earlier month's "
        "minimum-lot surplus, so nothing needs making. The line exists at "
        "quantity 0 so the cell reads \"already made\" rather than vanishing "
        "and reading as \"no demand\"."),
    "surplus_expiry_risk": (
        "The surplus on this line will sit in stock longer than its shelf life "
        "allows before later demand consumes it. A warning, not a block — the "
        "plant chose to round up."),
    "manual_adjusted": (
        "A planner changed this line by hand, so the quantity is not purely "
        "what the engine derived."),
    "locked_by_planner": (
        "A planner locked this line; re-runs leave it alone."),
}

# Reported when FALSE — the absence is the notable thing.
INVERTED_FLAGS: dict[str, str] = {
    "shelf_life_ok": (
        "The stock would not survive from production to the demand it covers."),
}


async def find_lines(db, material: str | None, month: str | None,
                     week: str | None, limit: int = 12) -> list[dict]:
    """Lines of the plan in force, narrowed by whatever the caller gave.

    Deliberately not routed through controlled_query: that returns rows for a
    reader, and this needs every derivation column whether or not anyone asked
    for it, including ones the ontology does not expose because they are of no
    use on their own.
    """
    conds = ["run_id IN (SELECT id FROM mrp_mps_runs WHERE is_default IS TRUE)"]
    params: dict = {}
    if material:
        conds.append("upper(material_code) = upper(:material)")
        params["material"] = material.strip()
    if month:
        conds.append("demand_month = :month")
        params["month"] = month.strip()
    if week:
        conds.append("plan_week_start = CAST(:week AS date)")
        params["week"] = week.strip()

    rows = (await db.execute(sa.text(
        "SELECT material_code, demand_month, plan_week_start, qty, "
        "demand_forecast, opening_stock, carry_in_qty, surplus_qty, "
        "weeks_early, is_prebuild, capacity_gap, lead_shortfall, below_min_lot, "
        "covered_by_carry, late_production, shelf_life_ok, surplus_expiry_risk, "
        "locked_by_planner, manual_adjusted, prebuild_reason "
        "FROM mrp_mps_lines WHERE " + " AND ".join(conds) +
        " ORDER BY plan_week_start, material_code LIMIT :lim"
    ), {**params, "lim": limit})).mappings().all()
    return [dict(r) for r in rows]


def _num(v) -> str | None:
    return None if v is None else str(Decimal(str(v)))


def explain(row: dict) -> dict:
    """The derivation behind one line, as steps a person can follow.

    Every figure is copied from the row. The one computed value is the residual,
    and it is computed only to be CHECKED against the engine's own quantity —
    if they disagree, that disagreement is what gets reported rather than a
    tidy story that happens to be wrong.
    """
    forecast = Decimal(str(row.get("demand_forecast") or 0))
    opening = Decimal(str(row.get("opening_stock") or 0))
    carry_in = Decimal(str(row.get("carry_in_qty") or 0))
    qty = Decimal(str(row.get("qty") or 0))
    surplus = Decimal(str(row.get("surplus_qty") or 0))

    plain = forecast - opening - carry_in
    if plain < 0:
        plain = Decimal(0)

    steps = [
        {"label": "Forecast demand for this month", "value": _num(forecast)},
        {"label": "Opening stock available", "value": _num(opening)},
    ]
    if carry_in:
        steps.append({"label": "Carried in from an earlier month's surplus",
                      "value": _num(carry_in)})
    steps.append({"label": "Net requirement (demand less what is already there)",
                  "value": _num(plain)})
    if surplus:
        steps.append({
            "label": "Of which exceeds the requirement, from rounding up to the "
                     "minimum lot size",
            "value": _num(surplus)})
    steps.append({"label": "Planned quantity", "value": _num(qty)})

    reasons = [{"flag": f, "meaning": FLAG_MEANINGS[f]}
               for f in FLAG_MEANINGS if row.get(f)]
    reasons += [{"flag": f, "meaning": m}
                for f, m in INVERTED_FLAGS.items() if row.get(f) is False]

    # Does the recorded arithmetic land on the engine's number?
    #
    # surplus_qty belongs in this sum, and leaving it out was a real bug: a line
    # of 14,910 against a net requirement of 7,020 with 7,890 of minimum-lot
    # surplus is fully accounted for — 7,020 + 7,890 — but the check reported it
    # as unexplained, and the reply told a planner the quantity could not be
    # derived and might be a display fault. Saying "I cannot account for this"
    # about a number that adds up is its own kind of wrong answer.
    matches = qty == plain + surplus
    return {
        "material_code": row.get("material_code"),
        "demand_month": row.get("demand_month"),
        "plan_week_start": (row["plan_week_start"].isoformat()
                            if row.get("plan_week_start") else None),
        "steps": steps,
        "arithmetic_accounts_for_it": matches,
        "weeks_early": row.get("weeks_early") or 0,
        "prebuild_reason": row.get("prebuild_reason"),
        "reasons": reasons,
        "unexplained": (not matches and not reasons),
    }
