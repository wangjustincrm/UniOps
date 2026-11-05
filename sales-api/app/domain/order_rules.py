"""Order rules in business terms (PRD 9.1, FR-OB-02/03d/12/14/15, D17, D20).

Pure functions over plain values: the services read the rows, these decide.
`qualifies` is the Python half of FR-OB-03d; the SQL predicate that must
agree with it, and the queueing that uses both, arrive with the 940 (P4).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.items import title_key
from app.domain.orders import FROM_945, PAYMENT_SATISFIED
from app.domain.status import FULFILLMENT
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.platform.values import ConversionError, FractionalQuantity, MissingConversion, Quantity, UnitTable

# ── order state (PRD 9.1, D17): derived, never stored ───────────────────────
ON_HOLD_FULFILLMENT = {F.REVIEW, F.PAYMENT_HOLD, F.ARCHIVE_SUPPRESSED, F.REJECTED_940, F.TIMEOUT_940,
                       F.FAILED_940, F.ON_HOLD}


def order_state(fulfillment: str, invoice: str) -> str:
    if fulfillment == F.DRAFT:
        return "Draft"
    if fulfillment == F.CANCELLED:
        return "Cancelled"
    if fulfillment in ON_HOLD_FULFILLMENT or invoice in (Inv.BLOCKED, Inv.FAILED):
        return "On Hold"
    if fulfillment == F.SHIPPED and invoice in (Inv.SENT, Inv.NOT_REQUIRED):
        return "Completed"
    return "Active"


# ── a person stopping an order (PRD 9.1, FR-OB-18, FR-OB-61) ─────────────────
def can_cancel(fulfillment: str) -> bool:
    """What the machine lets go to Cancelled; a draft has its own Cancel. Shown as in production;
    the server refuses a shadow order when it is used."""
    return fulfillment != F.DRAFT and FULFILLMENT.can(F(fulfillment), F.CANCELLED)


def hold_exits(fulfillment: str, before: str | None, *, cancelled_at_source: bool, applied_945: bool) -> list[str]:
    """Where a person can take an order On Hold. Cancelled at source: Cancelled, Shipped (a 945
    says it left) or what it held before. 945 conflict: keep the applied 945 (Shipped) or Cancelled."""
    if fulfillment != F.ON_HOLD:
        return []
    if not cancelled_at_source:
        return [F.SHIPPED.value, F.CANCELLED.value]
    exits = [F.CANCELLED.value, *([F.SHIPPED.value] if applied_945 else [])]
    return exits + ([before] if before and before not in exits else [])


# ── payment (FR-OB-03d, FR-OB-12) ────────────────────────────────────────────
def payment_satisfied(settlement_mode: str, source_type: str, financial_status: str | None) -> bool:
    """INVOICE partners are billed after delivery: no payment check. PREPAID:
    Shopify's financial status `paid` or `partially_refunded`. A submitted
    manual order counts as paid (a person entered it; there is no Shopify
    status to wait for)."""
    if settlement_mode == "INVOICE" or source_type == "MANUAL":
        return True
    return financial_status in PAYMENT_SATISFIED


# ── eligibility (FR-OB-03d) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class OrderFacts:
    fulfillment_status: str
    invoice_status: str
    source_type: str
    financial_status: str | None
    shopify_fulfillment_status: str | None
    archived: bool
    historical: bool
    first_received_at: datetime
    paid_at: datetime | None
    shadow: bool = False  # received during the shadow run (§5.4): Zoho handles it


@dataclass(frozen=True)
class PartnerFacts:
    settlement_mode: str
    sending_mode: str
    delay_minutes: int


def facts_of(order: Any, partner: Any) -> tuple[OrderFacts, PartnerFacts]:
    """The eligibility facts of an order row and its partner row."""
    return (OrderFacts(order.fulfillment_status, order.invoice_status, order.source_type, order.shopify_financial_status,
                       order.shopify_fulfillment_status, order.archived_in_shopify_at is not None, order.historical,
                       order.first_received_at, order.paid_at, bool(order.shadow)),
            PartnerFacts(partner.settlement_mode, partner.sending_mode, partner.delay_minutes))


def delay_start(o: OrderFacts, p: PartnerFacts) -> datetime | None:
    """D20: PREPAID from when payment became satisfied, INVOICE from receipt.
    Later source updates never move it."""
    return o.first_received_at if p.settlement_mode == "INVOICE" else o.paid_at


def qualifies(o: OrderFacts, p: PartnerFacts, now: datetime) -> tuple[bool, str]:
    """Every condition of FR-OB-03d (all must hold, no OR) → (qualifies, why not)."""
    checks = (
        (order_state(o.fulfillment_status, o.invoice_status) == "Active", "order state is not Active"),
        (o.fulfillment_status == F.PENDING, "fulfillment is not Pending"),
        (payment_satisfied(p.settlement_mode, o.source_type, o.financial_status), "payment not satisfied"),
        (o.shopify_fulfillment_status != "fulfilled", "already fulfilled"),
        (not o.archived, "archived in Shopify"),
        (not o.historical, "historical order"),
        (not o.shadow, "received during the shadow run (Zoho handles it)"),
        (o.source_type != FROM_945, "created from a 945: it has already shipped"),
        (p.sending_mode == "AUTOMATIC", "partner sends manually"),
    )
    for ok, why in checks:
        if not ok:
            return False, why
    start = delay_start(o, p)
    if start is None or now < start + timedelta(minutes=p.delay_minutes):
        return False, "delay not elapsed"
    return True, ""


# ── units (FR-OB-02, FR-OB-14, FR-OB-15) ─────────────────────────────────────
# A title that still looks like a pack — a number of 2 or more and a pack
# word — when no unit of the item matched: review, so a new pack size never
# ships as 1 EA (product owner, 2026-10-30).
_PACK_WORDS = {"case", "cases", "pack", "packs", "box", "boxes", "can", "cans", "ct", "x"}


def looks_like_pack(variant_title: str | None) -> bool:
    tokens = re.findall(r"[a-z]+|[0-9]+", (variant_title or "").lower())
    return any(t.isdigit() and int(t) >= 2 for t in tokens) and any(t in _PACK_WORDS for t in tokens)


def unit_from_title(variant_title: str | None, units: dict[str, Decimal],
                    titles: Mapping[str, Sequence[str]] | None = None) -> str | None:
    """The Shopify unit of a line: the variant title decides (product owner,
    2026-10-30; PRD A.11), the factor comes from the Item Master.

    The title is matched against each unit's name (not EA's) and its Shopify
    titles, compared by title_key, longest first. A title naming no unit is
    EA ("Bib", "Can", "Default Title") — unless it looks like a pack
    (looks_like_pack): then None, and the line goes to review."""
    candidates = [(title_key(name), name) for name in units if name != "EA"]
    candidates += [(title_key(t), name) for name in units for t in (titles or {}).get(name, ())]
    title = title_key(variant_title)
    for key, name in sorted(candidates, key=lambda c: len(c[0]), reverse=True):
        if key and key in title:
            return name
    return None if looks_like_pack(variant_title) else "EA"


def to_ea(quantity: Decimal, unit: str | None, units: dict[str, Decimal]) -> tuple[int | None, str | None]:
    """(EA quantity, problem). Never defaults a missing factor to 1 and never
    rounds (product owner, 2026-10-28)."""
    if unit is None:
        return None, "the unit could not be recognised"
    if quantity <= 0:
        return None, "zero quantity"
    table = UnitTable(base="EA", factors={u: f for u, f in units.items() if u != "EA"})
    try:
        return int(Quantity(quantity, unit).to_base(table).amount), None
    except MissingConversion:
        return None, f"no conversion for unit {unit}"
    except FractionalQuantity as e:
        return None, f"{quantity} {unit} is {e.base_amount} EA, not a whole number"
    except ConversionError as e:  # pragma: no cover — the two above are all there are
        return None, str(e)
