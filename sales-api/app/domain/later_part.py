"""A second, different 945 of an order that shipped (user 2026-11-25, design accepted): the part the first one left, one that
is more than the 940 carried, or neither — with the reason. One rule: the 945 pass adds a part by itself, a person adds either
in Compare the 945s. Pure: the service (services/later_part.py) reads the facts.

A 945 above the 940 is never added by the pass: a corrected 945 (the same item, another lot or date) cannot be told from an
extra one, and would take the stock twice. Everything else that refuses a part refuses it too.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv

PART, MORE = "part", "more"
JOB, PRESS = "job", "press"
# No invoice of it is built or stopped yet. (A Shopify order's is Not Required; the rest is for when others are allowed.)
NOT_INVOICED = (Inv.NOT_REQUIRED.value, Inv.PENDING.value, Inv.AWAITING_REQUESTED_DATE.value,
                Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value)


@dataclass(frozen=True)
class ItemFigures:
    """One item of this 945 over its order lines: EA on the 940, shipped by the 945s before, and in this one."""
    item_code: str
    carried: int
    earlier: int
    this: int

    @property
    def above(self) -> int:
        """EA above what the 940 carried, once this 945 counts."""
        return max(self.earlier + self.this - self.carried, 0)


@dataclass(frozen=True)
class Facts:
    source_type: str
    fulfillment: str
    held_from: str | None              # what the order was before its hold
    cancelled_at_source: bool
    historical: bool
    shadow: bool
    own_940: bool                      # Sales sent it, and it says what it carried
    invoice: str
    invoiced: bool                     # an invoice or a delivery confirmation exists
    child: bool                        # an order was made from it (a reorder, a gift)
    returned: tuple[str, ...] = ()     # this 945's items with a refunded line or a recorded return
    adjusted: tuple[str, ...] = ()     # this 945's items with stock taken out by a person since the first 945
    unmatched: tuple[str, ...] = ()    # rows that match no line of the 940
    waiting: tuple[str, ...] = ()      # other 945s of the order, different and not applied
    items: tuple[ItemFigures, ...] = ()


@dataclass(frozen=True)
class Answer:
    kind: str | None                   # PART, MORE or neither
    refused: str | None = None         # why this is not added (a MORE for the pass: why not by itself)
    items: tuple[ItemFigures, ...] = field(default=())

    @property
    def over(self) -> tuple[ItemFigures, ...]:
        return tuple(i for i in self.items if i.above)


def _why_not(f: Facts, by: str) -> str | None:
    """What refuses it, whatever its quantities; the first that does."""
    if f.source_type != "SHOPIFY":
        return "only a Shopify order's later 945 is added in Sales for now"
    if f.shadow or f.historical:
        return "the order is an old one, or Zoho's"
    if not f.own_940:
        return "Sales sent no 940 for it, so what was still to come is not known"
    if f.cancelled_at_source:
        return "the order is cancelled at source"
    if by == JOB and f.fulfillment != F.SHIPPED:
        return f"the order is {f.fulfillment}, not Shipped"
    if by == PRESS and (f.fulfillment != F.ON_HOLD or f.held_from != F.SHIPPED):
        return "the order is not On Hold since it shipped"
    if by == PRESS and f.waiting:
        return (f"another 945 of it was not applied either ({', '.join(f.waiting)}; one that was kept out counts too), and which of them left is not for "
                "Sales to say. Keep the applied 945, ask the warehouse, and if goods did leave beyond the order, correct the stock with an adjustment")
    if f.child:
        return "an order was made from this one (a reorder): the 945 may be that order's"
    if f.invoiced or f.invoice not in NOT_INVOICED:
        return f"its invoice or its delivery is already settled or stopped (invoice: {f.invoice})"
    if f.returned:
        return f"a refund or a return is recorded for {', '.join(f.returned)}"
    if f.adjusted:
        return f"stock of {', '.join(f.adjusted)} was adjusted out by a person since the first 945: it would be taken out twice"
    if f.unmatched:
        return "rows match no line of the 940: " + "; ".join(f.unmatched)
    return None if f.items else "it carries nothing"


def answer(f: Facts, *, by: str) -> Answer:
    if why := _why_not(f, by):
        return Answer(None, why, f.items)
    over = [i for i in f.items if i.above]
    if not over:
        return Answer(PART, None, f.items)
    return Answer(MORE, None if by == PRESS else "it is more than the 940 carried: " + above_words(over), f.items)


def above_words(items) -> str:
    """Item by item: "CF0093: 1 EA above the 24 on the 940 (25 with this 945)"."""
    return "; ".join(f"{i.item_code}: {i.above} EA above the {i.carried} on the 940 ({i.earlier + i.this} with this 945)" for i in items)


__all__ = ["Answer", "Facts", "ItemFigures", "JOB", "MORE", "PART", "PRESS", "above_words", "answer"]
