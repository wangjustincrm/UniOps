"""Phase 1C's computation core — pure functions, no DB and no clock.

The chain, in the order a planner would describe it:

    production plan (product x week)
      -> explode BOMs                 `explode_demands`
      -> inflate for loss             (inside the explosion, per component)
      -> net off stock, week by week  `net_off_inventory`
      -> work back to an order date   `plan_orders`
      -> round up to MOQ / multiple   (inside `plan_orders`)

Kept free of the database so the arithmetic can be pinned by tests that
need no fixtures: every trap this module has to avoid is arithmetic.

## Traps this module is written around

- **BOM quantities are already per-unit.** NC stores a whole BATCH quantity
  and the batch size in the header; the sync divides by it. Dividing again
  here is off by orders of magnitude (a real case: 113,337,630 vs 0.2699).
- **Loss is per component category, applied once.** A component takes the
  rate for ITS OWN category at whatever level it sits; ancestors' rates are
  never compounded into it.
- **Stock does not renew every week.** On-hand covers the earliest weeks
  first and what is left carries forward. Netting each week against the
  full stock figure independently would buy far too little.
- **A missing lead time is not zero.** It is computed as zero and FLAGGED,
  because an order date that silently equals the need date tells purchasing
  the goods can be had on demand.
"""
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from app.services.loss_rate import applicable_loss_rate, inflate_for_loss


@dataclass(frozen=True)
class ComponentDemand:
    """One component's requirement for one week."""
    material_code: str
    week: date
    gross: Decimal
    available: Decimal = Decimal("0")
    net: Decimal = Decimal("0")
    # The product had no BOM. Carried as a zero-quantity row rather than
    # dropped: "we could not explode this" is information a planner needs,
    # and a silently shorter list looks like a complete one.
    missing_bom: bool = False
    # Convenience alias used by the explosion step, where "gross" is the
    # only quantity that exists yet.
    @property
    def qty(self) -> Decimal:
        return self.gross


@dataclass(frozen=True)
class SupplyParams:
    partner_code: str | None = None
    lead_time_days: int | None = None
    moq: Decimal | None = None
    order_multiple: Decimal | None = None


@dataclass(frozen=True)
class PurchaseOrderSuggestion:
    material_code: str
    need_week: date
    order_date: date
    gross: Decimal
    available: Decimal
    net: Decimal
    suggested_qty: Decimal
    partner_code: str | None
    lead_time_days: int | None
    raised_to_moq: Decimal = Decimal("0")
    supplier_missing: bool = False
    lead_time_missing: bool = False
    order_date_passed: bool = False


BomLookup = Callable[[str], list[tuple[str, Decimal]]]


def explode_demands(
    demands: list[tuple[str, date, Decimal]],
    bom_lookup: BomLookup,
    rates: dict[str, Decimal],
    max_depth: int = 10,
) -> list[ComponentDemand]:
    """Explode each product's requirement into component requirements.

    `demands` is `(product_code, week, quantity)`; `bom_lookup` answers a
    product with `[(component_code, qty_per_unit), ...]` — **already per
    unit**, see this module's docstring.

    Each component is inflated by the rate for its own category as it is
    produced, so a sub-component is inflated once (by its own rate), never
    by its parent's as well.

    A product with no BOM yields one `missing_bom` row of quantity 0 rather
    than nothing at all.
    """
    totals: dict[tuple[str, date], Decimal] = {}
    missing: list[ComponentDemand] = []

    def _walk(code: str, week: date, quantity: Decimal, depth: int) -> None:
        components = bom_lookup(code)
        if not components:
            return
        for component_code, per_unit in components:
            base = quantity * per_unit
            rate = applicable_loss_rate(component_code, rates)
            inflated = inflate_for_loss(base, rate)
            key = (component_code, week)
            totals[key] = totals.get(key, Decimal("0")) + inflated
            if depth < max_depth:
                # Children are exploded off the BASE quantity, not the
                # inflated one: each level's loss belongs to that level's
                # own component, and multiplying it down the tree inflates
                # deep components several times over.
                _walk(component_code, week, base, depth + 1)

    for product_code, week, quantity in demands:
        if not bom_lookup(product_code):
            missing.append(ComponentDemand(product_code, week, Decimal("0"),
                                           missing_bom=True))
            continue
        _walk(product_code, week, quantity, 1)

    exploded = [ComponentDemand(code, week, qty)
                for (code, week), qty in sorted(totals.items())]
    return exploded + missing


def net_off_inventory(
    demands: list[ComponentDemand], on_hand: dict[str, Decimal],
) -> list[ComponentDemand]:
    """Draw each material's stock down across its weeks, earliest first.

    Stock is a single pool that empties, not a figure that renews weekly:
    50 kg covers a 30 kg week and leaves 20 kg for the next one. Netting
    every week against the full 50 would report no requirement at all and
    buy nothing.
    """
    remaining = dict(on_hand)
    out: list[ComponentDemand] = []
    for demand in sorted(demands, key=lambda d: (d.material_code, d.week)):
        pool = remaining.get(demand.material_code, Decimal("0"))
        used = pool if pool < demand.gross else demand.gross
        remaining[demand.material_code] = pool - used
        out.append(replace(demand, available=pool, net=demand.gross - used))
    return out


def _round_up_to_multiple(quantity: Decimal, multiple: Decimal) -> Decimal:
    if multiple <= 0:
        return quantity
    steps = (quantity / multiple).to_integral_value(rounding="ROUND_CEILING")
    return steps * multiple


def plan_orders(
    demands: list[ComponentDemand],
    supply: dict[str, SupplyParams],
    today: date | None = None,
) -> list[PurchaseOrderSuggestion]:
    """Turn net requirements into order suggestions.

    Weeks that need nothing produce no row: a list padded with zeroes is a
    list nobody reads.

    Quantities are raised to the minimum order quantity and then rounded up
    to the order multiple — in that order, because a multiple applied first
    can land below the MOQ and the supplier would refuse it.

    Everything unknown is flagged rather than assumed: no supplier, no lead
    time, and an order date that has already passed each carry their own
    flag, so the screen can say which numbers are load-bearing and which
    are placeholders.
    """
    suggestions: list[PurchaseOrderSuggestion] = []
    for demand in demands:
        if demand.net <= 0 or demand.missing_bom:
            continue
        params = supply.get(demand.material_code, SupplyParams())
        lead_days = params.lead_time_days if params.lead_time_days is not None else 0
        order_date = demand.week - timedelta(days=lead_days)

        quantity = demand.net
        raised = Decimal("0")
        if params.moq is not None and quantity < params.moq:
            raised = params.moq - quantity
            quantity = params.moq
        if params.order_multiple is not None:
            quantity = _round_up_to_multiple(quantity, params.order_multiple)

        suggestions.append(PurchaseOrderSuggestion(
            material_code=demand.material_code,
            need_week=demand.week,
            order_date=order_date,
            gross=demand.gross,
            available=demand.available,
            net=demand.net,
            suggested_qty=quantity,
            partner_code=params.partner_code,
            lead_time_days=params.lead_time_days,
            raised_to_moq=raised,
            supplier_missing=params.partner_code is None,
            lead_time_missing=params.lead_time_days is None,
            order_date_passed=today is not None and order_date < today,
        ))
    return suggestions
