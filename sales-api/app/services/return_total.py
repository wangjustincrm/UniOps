"""What came back of a line is at most what shipped (user 2026-11-24, 2026-11-25): what the customer returned on the order (the
line's returned_ea) and what is recorded after shipment (OrderReturn, both kinds, all records) together. One rule, both in EA per
order line, for both ways in: set_returned and record_return."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.models.invoices import OrderReturn
from app.models.orders import OrderLine
from app.platform.errors import BusinessError


def _total(entries: Iterable[dict[str, Any]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for x in entries:
        out[int(x["order_line_id"])] = out.get(int(x["order_line_id"]), 0) + int(x["qty_ea"])
    return out


async def refuse_more_than_shipped(s, order_id: int, *, returned: dict[int, int] | None = None, recording: Iterable[dict[str, Any]] = ()) -> None:
    """Refuse what would go above it: `returned` — lines whose returned_ea becomes this; `recording` — the entries a record adds.
    Only a figure that rises is refused: lowering one, or leaving it, always passes, also on a line that is above already.
    The caller holds the order's lock; both figures are read here, under it, from the database."""
    returned, asked = returned or {}, _total(recording)
    recorded = _total(x for (was,) in (await s.execute(select(OrderReturn.lines).where(OrderReturn.order_id == order_id))).all() for x in was)
    lines = {r.id: r for r in (await s.execute(select(OrderLine.id, OrderLine.line_no, OrderLine.shipped_ea, OrderLine.returned_ea).where(
        OrderLine.order_id == order_id, OrderLine.id.in_({*returned, *asked})))).all()}
    for id in dict.fromkeys((*returned, *asked)):  # in the request's order
        _, no, shipped, now = lines[id]
        on_order, more, was = returned.get(id, now), asked.get(id, 0), recorded.get(id, 0)
        if (on_order > now or more) and on_order + was + more > (shipped or 0):
            raise BusinessError("return.more_than_shipped", f"line {no}: {on_order} EA returned on the order + {was} EA already recorded"
                                + (f" + {more} EA asked" if more else "") + f" is more than the {shipped or 0} EA shipped",
                                {"line_id": id, "field": "qty_ea" if more else "returned_ea"}, status_code=422)


__all__ = ["refuse_more_than_shipped"]
