"""Wires Phase 1C's arithmetic (`purchase_engine`) to real data.

Everything that talks to a database, another service or the clock lives
here; `purchase_engine` stays pure. The split matters because the parts
that go wrong are different: the arithmetic goes wrong quietly (an order of
magnitude in a BOM quantity), the plumbing goes wrong loudly (a service is
down), and they want different kinds of test.

Sources, in the order the calculation needs them:

- **What to make** — `mrp_demands` rows of the plan in force. That is the
  released quantity, minimum-lot surplus included, which is exactly what
  materials have to be bought for.
- **What it is made of** — mdm-api's BOM explosion, fetched once per
  finished product and flattened into an adjacency map so the engine's own
  recursion runs in memory rather than over HTTP.
- **How much loss to add** — the planning parameters, snapshotted onto the
  run.
- **What is already here** — the same opening-stock definition net
  requirements use, so the two never disagree about what "available" means.
- **Who to buy it from** — mdm-api's material_suppliers, primary first.
"""
from datetime import date
from decimal import Decimal

import httpx

import sqlalchemy as sa

from app.core.config import settings
from app.models.demand import MrpDemand
from app.services.loss_rate import resolve_loss_rates
from app.services.net_requirement import get_opening_stock_breakdown
from app.services.purchase_engine import (
    PurchaseOrderSuggestion, SupplyParams, explode_demands, net_off_inventory, plan_orders,
)


async def load_plan_demands(db) -> list[tuple[str, date, Decimal]]:
    """`(product, week, quantity)` from the demand set 1C purchases against."""

    rows = (await db.execute(
        sa.select(MrpDemand)
        .where(MrpDemand.demand_type == "mps")
        .order_by(MrpDemand.material_code, MrpDemand.plan_week_start)
    )).scalars().all()
    return [(r.material_code, r.plan_week_start, r.qty) for r in rows]


def _flatten_tree(node: dict, into: dict[str, list[tuple[str, Decimal]]]) -> None:
    """Turn mdm's explosion tree into `{parent: [(child, qty_per), ...]}`.

    `qty_per` is the child's quantity per ONE unit of its immediate parent —
    the per-level figure, not the accumulated one. The engine multiplies
    down the tree itself, and feeding it accumulated quantities would apply
    the whole chain twice.
    """
    children = node.get("children") or []
    if children:
        into[node["material_code"]] = [
            (child["material_code"], Decimal(str(child["qty_per"]))) for child in children
        ]
    for child in children:
        _flatten_tree(child, into)


async def fetch_bom_adjacency(token: str, product_codes: list[str],
                              on_date: date) -> dict[str, list[tuple[str, Decimal]]]:
    """One explosion request per finished product, flattened and merged.

    A product whose explosion fails or has no BOM simply contributes
    nothing; the engine then reports it as `missing_bom` rather than this
    layer deciding what an absent BOM means.
    """
    adjacency: dict[str, list[tuple[str, Decimal]]] = {}
    async with httpx.AsyncClient(
        base_url=f"{settings.MDM_API_URL}/mdm/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    ) as client:
        for code in product_codes:
            try:
                response = await client.get(
                    "/boms/explode",
                    params={"product_material_code": code, "on_date": on_date.isoformat()},
                )
                if response.status_code != 200:
                    continue
                _flatten_tree(response.json(), adjacency)
            except httpx.HTTPError:
                # mdm-api being unreachable must not silently produce a plan
                # with no materials in it; the caller counts the products it
                # asked for against the ones that came back (see `stats`).
                continue
    return adjacency


async def fetch_supply_params(token: str) -> dict[str, SupplyParams]:
    """Supply parameters by material, primary supplier winning.

    A material with suppliers but no primary still gets one (the first by
    code) so the suggestion carries a name rather than nothing -- and the
    Supply Parameters screen counts those materials so somebody can settle
    which is primary.
    """
    chosen: dict[str, SupplyParams] = {}
    primary_seen: set[str] = set()
    async with httpx.AsyncClient(
        base_url=f"{settings.MDM_API_URL}/mdm/v1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    ) as client:
        page = 1
        while True:
            try:
                response = await client.get(
                    "/material-suppliers", params={"page": page, "page_size": 500})
            except httpx.HTTPError:
                break
            if response.status_code != 200:
                break
            body = response.json()
            for row in body.get("items", []):
                code = row["material_code"]
                if code in primary_seen and not row["is_primary"]:
                    continue
                if row["is_primary"]:
                    primary_seen.add(code)
                chosen[code] = SupplyParams(
                    partner_code=row.get("partner_code"),
                    lead_time_days=row.get("lead_time_days"),
                    moq=Decimal(str(row["moq"])) if row.get("moq") is not None else None,
                    order_multiple=(Decimal(str(row["order_multiple"]))
                                    if row.get("order_multiple") is not None else None),
                )
            if len(body.get("items", [])) < body.get("page_size", 0):
                break
            page += 1
    return chosen


async def compute_suggestions(
    db, token: str, *, today: date,
) -> tuple[list[PurchaseOrderSuggestion], dict]:
    """Run the whole chain and return the suggestions plus a summary.

    The summary is not decoration: it is how a planner tells "nothing needs
    buying" apart from "the BOM service was down and nothing exploded".
    """
    demands = await load_plan_demands(db)
    products = sorted({code for code, _, _ in demands})
    rates = await resolve_loss_rates(db)

    earliest = min((week for _, week, _ in demands), default=today)
    adjacency = await fetch_bom_adjacency(token, products, earliest)

    exploded = explode_demands(demands, lambda code: adjacency.get(code, []), rates)
    on_hand: dict[str, Decimal] = {}
    for material_code in sorted({line.material_code for line in exploded}):
        breakdown = await get_opening_stock_breakdown(db, material_code)
        on_hand[material_code] = breakdown.opening_stock

    netted = net_off_inventory([l for l in exploded if not l.missing_bom], on_hand)
    supply = await fetch_supply_params(token)
    suggestions = plan_orders(netted, supply, today=today)

    stats = {
        "products_in_plan": len(products),
        "products_without_bom": len({l.material_code for l in exploded if l.missing_bom}),
        "components": len({l.material_code for l in netted}),
        "lines": len(suggestions),
        "supplier_missing": sum(1 for s in suggestions if s.supplier_missing),
        "lead_time_missing": sum(1 for s in suggestions if s.lead_time_missing),
        "order_date_passed": sum(1 for s in suggestions if s.order_date_passed),
        "raw_material_loss_rate": str(rates["raw"]),
        "packaging_loss_rate": str(rates["packaging"]),
    }
    return suggestions, stats
