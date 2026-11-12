"""Closes old Shopify orders that Sales still shows open, from the store's own state.

After the Zoho history replay, some Shopify orders stop at 940 Accepted / Rejected /
Timeout: Zoho's export has no 945 for them, but the store finished them long ago; and
old orders read from the store stop at Payment Hold / Archive Suppressed (never paid,
or archived). Historical or not, each is settled from the store.
This reads each one from Shopify (read only: the order and its fulfillments) and
  - fulfilled in the store (or closed with one)    → Shipped
  - cancelled, or refunded in full with no tracking,
    or closed without a fulfillment                → Cancelled
  - anything else (still open in the store)        → left as it is, and listed
and marks it historical, so no job ever picks it up again. Its open 940 / review
tasks are closed. Nothing is written to Shopify and no file is made or sent.
Only orders dated before --before are looked at. Idempotent.

    python -m app.scripts.close_from_shopify --before 2026-11-01           # dry run
    python -m app.scripts.close_from_shopify --before 2026-11-01 --apply   # close them
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.status import FULFILLMENT
from app.domain.status import Fulfillment as F
from app.integrations.shopify import HttpShopify
from app.models.config import Connection
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.order_holds import ORDER_PROBLEMS
from app.services.problems import task_service

# Not finished: a 940 out with no answer applied, or stopped before any 940 (unpaid, archived).
OPEN = (F.SENT_940, F.ACCEPTED_940, F.REJECTED_940, F.TIMEOUT_940, F.PAYMENT_HOLD, F.ARCHIVE_SUPPRESSED)


@dataclass(frozen=True)
class Decision:
    to: F | None
    why: str


def decide(store: dict, fulfillments: list[dict]) -> Decision:
    """What the store says became of the order (no PII is read: statuses and counts only)."""
    tracking = sum(len(f.get("tracking_numbers") or []) for f in fulfillments)
    if store.get("cancelled_at"):
        return Decision(F.CANCELLED, "cancelled in the store")
    if store.get("fulfillment_status") == "fulfilled":
        if store.get("financial_status") == "refunded" and not tracking:
            return Decision(F.CANCELLED, "refunded in full in the store, no tracking number")
        return Decision(F.SHIPPED, "fulfilled in the store")
    if store.get("closed_at"):
        if not fulfillments:
            return Decision(F.CANCELLED, "closed in the store without a fulfillment")
        return Decision(F.SHIPPED, "closed in the store with a fulfillment")
    return Decision(None, "still open in the store")


def path(src: F, dst: F) -> list[F] | None:
    """The status steps from src to dst the state machine allows (direct, or through On Hold)."""
    if FULFILLMENT.can(src, dst):
        return [dst]
    if FULFILLMENT.can(src, F.ON_HOLD) and FULFILLMENT.can(F.ON_HOLD, dst, held_before=src):
        return [F.ON_HOLD, dst]
    return None


async def main(args: ScriptArgs) -> dict:
    before = date.fromisoformat(args.value("before") or "")
    shopify = HttpShopify(secret=os.environ.get)
    tasks = task_service(lambda: False)
    out: list[dict] = []
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        orders = list((await s.execute(select(Order).where(
            Order.source_type == "SHOPIFY", Order.shadow.is_(False),
            Order.fulfillment_status.in_([x.value for x in OPEN]), Order.order_date < before)
            .order_by(Order.order_date, Order.id).with_for_update())).scalars())
        conns: dict[str, ConnectionSnapshot] = {}
        for o in orders:
            if o.partner_code not in conns:
                c = (await s.execute(select(Connection).where(Connection.kind == "SHOPIFY", Connection.partner_code == o.partner_code)
                                     .order_by(Connection.active.desc(), Connection.id).limit(1))).scalar_one()
                conns[o.partner_code] = ConnectionSnapshot.of(c)
            snap = conns[o.partner_code]
            store = await shopify.order(snap, o.source_order_id)
            fulfilled = await shopify.fulfillments(snap, o.source_order_id)
            src = F(o.fulfillment_status)
            d = decide(store, fulfilled)
            row = {"order_id": o.id, "order": o.label, "from": src.value, "to": d.to.value if d.to else None, "why": d.why}
            steps = path(src, d.to) if d.to else None
            if d.to and steps is None:
                row["to"], row["why"] = None, f"{d.why}: {src.value} → {d.to.value} is not allowed, left as it is"
            out.append(row)
            if not args.apply or not steps:
                continue
            reason = f"closed from the store's state (history): {d.why}"
            for step in steps:
                FULFILLMENT.transition(o, step, actor=u.actor, events=u.events, reason=reason)
            o.hold_reason = None
            o.historical = True
            for event in ORDER_PROBLEMS:
                await tasks.resolve_problem(s, event, o.id)
            await oplog.write(s, "INFO", "repair", f"order {o.label} {reason}", order_id=o.id)
        if args.apply:
            await u.commit()
    closed = [r for r in out if r["to"]]
    return {"looked_at": len(out), "closed": len(closed),
            "shipped": sum(r["to"] == F.SHIPPED for r in closed), "cancelled": sum(r["to"] == F.CANCELLED for r in closed),
            "left_open": [r for r in out if not r["to"]], "detail": out}


if __name__ == "__main__":
    script_main(main, takes_values=("before",))
