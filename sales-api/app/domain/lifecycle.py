"""An order line's lifecycle in one row (the order detail's Lines table):
ordered → shipped (945) → delivered (confirmation) → invoiced."""
from __future__ import annotations

LINE_STATES = ("Removed", "Open", "Short shipped", "Over shipped", "Shipped", "Short delivered", "Delivered", "Invoiced")


def line_state(*, removed: bool, qty_ea: int | None, shipped_ea: int | None, delivered_ea: int | None,
               invoiced: bool) -> str:
    """Where a line is, the furthest step reached; a quantity short of the
    order (or over it) says so at the step it happened."""
    if removed:
        return "Removed"
    if invoiced:
        return "Invoiced"
    if delivered_ea is not None:
        return "Short delivered" if qty_ea is not None and delivered_ea < qty_ea else "Delivered"
    if shipped_ea is not None:
        if qty_ea is not None and shipped_ea < qty_ea:
            return "Short shipped"
        if qty_ea is not None and shipped_ea > qty_ea:
            return "Over shipped"
        return "Shipped"
    return "Open"


def backordered(qty_ea: int | None, shipped_ea: int | None, removed: bool) -> int | None:
    """A line's backordered EA: ordered − shipped once an applied 945 set the
    shipped quantity (never below 0); none before, for a removed line, or
    without an EA quantity. Shadow-run and second 945s never set it."""
    if removed or qty_ea is None or shipped_ea is None:
        return None
    return max(qty_ea - shipped_ea, 0)


def spread(shipped: int, quantities: list[int]) -> list[int]:
    """What shipped of one item across the lines of that item, in line order: each up
    to its quantity, what is left over on the first (over-shipped). A 945 row links to one
    line, but fills several when an order has the item twice, so a line's own rows do not
    say what it got: the item's total does (the 945 plan fills lines the same way)."""
    out, left = [], shipped
    for q in quantities:
        take = min(left, max(q, 0))
        out.append(take)
        left -= take
    if out:
        out[0] += left
    return out

