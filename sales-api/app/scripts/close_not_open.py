"""Closes the old retailer orders that are no longer open, from the list of the ones that are.

After the Zoho history replay, some retailer orders stay open in Sales although
the business closed them (user 2026-11-11: "only these are open"). Given the
POs that ARE open (one per line in a file), every other non-historical
retailer order dated before --before is closed:
  - its 940 sent and no 945 applied        → Cancelled (through On Hold when needed)
  - shipped, invoice not sent from Sales   → invoice Sent (invoiced outside Sales:
                                              by Zoho, or by email), through Ready
and marked historical, so no job picks it up again; its open order / invoice
tasks are closed. No 810, no email, no file is made or sent. Idempotent.

    python -m app.scripts.close_not_open <open POs file> --before 2026-11-01           # dry run
    python -m app.scripts.close_not_open <open POs file> --before 2026-11-01 --apply
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.status import FULFILLMENT, INVOICE
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.orders import Order
from app.models.shipments import Shipment
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.order_holds import ORDER_PROBLEMS
from app.services.problems import INVOICE_FAILED, INVOICE_HOLD, SHORT_SHIPMENT, task_service

NOT_SHIPPED = (F.SENT_940, F.ACCEPTED_940, F.REJECTED_940, F.TIMEOUT_940)
SETTLED = ORDER_PROBLEMS + (INVOICE_HOLD, INVOICE_FAILED, SHORT_SHIPMENT)


def fulfillment_steps(src: F) -> list[F]:
    if FULFILLMENT.can(src, F.CANCELLED):
        return [F.CANCELLED]
    if FULFILLMENT.can(src, F.ON_HOLD) and FULFILLMENT.can(F.ON_HOLD, F.CANCELLED, held_before=src):
        return [F.ON_HOLD, F.CANCELLED]
    raise ValueError(f"{src.value} cannot be cancelled")


def invoice_steps(src: Inv, held_before: Inv | None) -> list[Inv]:
    """To Sent along the invoice table: Blocked returns to what it held, then on through Ready."""
    path: list[Inv] = []
    cur = src
    if cur is Inv.BLOCKED:
        if held_before is None:
            raise ValueError("a Blocked invoice without the status it held")
        path.append(held_before)
        cur = held_before
    order = [Inv.PENDING, Inv.AWAITING_REQUESTED_DATE, Inv.WAITING_FOR_DELIVERY_CONFIRMATION, Inv.READY, Inv.SENT]
    if cur not in order:
        raise ValueError(f"invoice {cur.value} cannot be closed here")
    for nxt in order[order.index(cur) + 1:]:
        if nxt is Inv.AWAITING_REQUESTED_DATE and not INVOICE.can(cur, nxt):
            continue
        if not INVOICE.can(cur, nxt):
            continue
        path.append(nxt)
        cur = nxt
    if cur is not Inv.SENT:
        raise ValueError(f"no path from {src.value} to Sent")
    return path


async def main(args: ScriptArgs) -> dict:
    keep = {line.strip().lstrip("0") for line in Path(args.positional[0]).read_text().splitlines() if line.strip()}
    before = date.fromisoformat(args.value("before") or "")
    tasks = task_service(lambda: False)
    out: list[dict] = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        orders = list((await s.execute(select(Order).where(
            Order.source_type == "EDI_850", Order.historical.is_(False), Order.shadow.is_(False),
            Order.fulfillment_status != F.CANCELLED.value, Order.order_date < before)
            .order_by(Order.order_date, Order.id).with_for_update())).scalars())
        open_here = {str(o.source_order_number).lstrip("0") for o in orders}
        for o in orders:
            if str(o.source_order_number).lstrip("0") in keep:
                continue
            src = F(o.fulfillment_status)
            applied = (await s.execute(select(Shipment.id).where(Shipment.order_id == o.id, Shipment.applied.is_(True)))).first()
            row = {"order_id": o.id, "order": o.label, "partner": o.partner_code, "date": str(o.order_date),
                   "fulfillment": src.value, "invoice": o.invoice_status}
            try:
                if src in NOT_SHIPPED and applied is None:
                    f_steps, i_steps, why = fulfillment_steps(src), [], "not open (user's list); no 945 for it"
                elif src is F.SHIPPED:
                    held = Inv(o.invoice_status_before_hold) if o.invoice_status_before_hold else None
                    f_steps, why = [], "not open (user's list); invoiced outside Sales"
                    i_steps = [] if o.invoice_status == Inv.SENT else invoice_steps(Inv(o.invoice_status), held)
                else:
                    raise ValueError(f"{src.value} is not closed by this script")
            except ValueError as e:
                row["left"] = str(e)
                out.append(row)
                continue
            row["to"] = (f_steps[-1].value if f_steps else src.value) + (f" / invoice {i_steps[-1].value}" if i_steps else "")
            out.append(row)
            if not args.apply:
                continue
            reason = f"closed (history): {why}"
            for st in f_steps:
                FULFILLMENT.transition(o, st, actor=u.actor, events=u.events, reason=reason)
            for st in i_steps:
                INVOICE.transition(o, st, actor=u.actor, events=u.events, reason=reason)
            o.hold_reason = None
            o.historical = True
            for event in SETTLED:
                await tasks.resolve_problem(s, event, o.id)
            await oplog.write(s, "INFO", "repair", f"order {o.label} {reason}", order_id=o.id)
        if args.apply:
            await u.commit()
    missing = sorted(keep - open_here)
    return {"open_list": len(keep), "open_list_not_open_here": missing,
            "closed": sum(1 for r in out if "to" in r), "left": [r for r in out if "left" in r], "detail": out}


if __name__ == "__main__":
    script_main(main, takes_values=("before",))
