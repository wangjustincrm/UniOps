"""Payment: a finance user marks an order Settled, or undoes the mark (user 2026-11-19 / 2026-11-23).

"For now it is manual with a finance user", for every source (retailer, manual, Shopify): the date the money came and
a reference (bank / cheque / remittance no.) are required, an amount and a note are not. Who marked it and when are
kept on the order; the mark and its undo (with a reason) are in the order's Activity (the audit). Nothing is sent to
anyone and the order's state does not change (Completed does not wait for Settled: the user has not decided).

sales.settle only — a finance key, not on sales.read or sales.order.operate. Shadow orders are Zoho's (refused, as
every action on them). A later derived source (Shopify from the gateway settlements) goes beside the manual columns;
the manual mark wins (app/domain/order_payment.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.order_payment import SETTLED
from app.domain.schedule import TORONTO
from app.domain.shadow import TEST_RUN_REFUSAL
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.platform.authorization import requires
from app.platform.commands import ANY_VERSION, VersionArg, check_version, load_for_command
from app.platform.errors import BusinessError, InvalidInput
from app.platform.services import Service
from app.services import oplog

SETTLE = "sales.settle"
STEP = "payment"


class OrderPaymentService(Service):
    async def _order(self, id: int, version: VersionArg) -> Order:
        o = await load_for_command(self.s, Order, id, version=ANY_VERSION, what="this order")
        if o.shadow:
            raise BusinessError("order.shadow", TEST_RUN_REFUSAL)
        check_version(o, version, "this order")
        return o

    async def _settle(self, o: Order, body) -> None:
        if o.fulfillment_status == F.DRAFT.value:
            raise BusinessError("order.draft", "a draft is not an order yet: submit it first")
        if o.settled_on is not None:
            raise BusinessError("order.settled", f"already settled on {o.settled_on:%Y-%m-%d} ({o.settled_reference})",
                                {"settled_on": str(o.settled_on), "reference": o.settled_reference})
        if body.settled_on > datetime.now(TORONTO).date():
            raise InvalidInput("order.settled_future", "the settled date cannot be in the future", {"field": "settled_on"})
        actor = self.uow.actor
        o.settled_on, o.settled_reference, o.settled_amount = body.settled_on, body.reference, body.amount
        o.settled_note = body.note or None
        o.settled_at, o.settled_by, o.settled_by_name = datetime.now(timezone.utc), actor.user_id, actor.name
        what = f"settled on {body.settled_on:%Y-%m-%d}, {body.reference}" + (f", {body.amount:.2f}" if body.amount is not None else "")
        self.uow.set_reason(what + (f": {body.note}" if body.note else ""))
        await oplog.write(self.s, "INFO", STEP, f"order {o.label} {what}", order_id=o.id)

    @requires(SETTLE)
    async def settle(self, id: int, body, version: VersionArg = ANY_VERSION) -> Order:
        o = await self._order(id, version)
        await self._settle(o, body)
        await self.s.flush()
        return o

    @requires(SETTLE)
    async def unsettle(self, id: int, reason: str, version: VersionArg = ANY_VERSION) -> Order:
        o = await self._order(id, version)
        if o.settled_on is None:
            raise BusinessError("order.not_settled", "this order is not marked settled")
        was = f"{o.settled_on:%Y-%m-%d}, {o.settled_reference}"
        o.settled_on = o.settled_reference = o.settled_amount = o.settled_note = None
        o.settled_at = o.settled_by = o.settled_by_name = None
        self.uow.set_reason(f"settlement undone (was {was}): {reason}")
        await oplog.write(self.s, "INFO", STEP, f"order {o.label}: settlement undone (was {was}): {reason}", order_id=o.id)
        await self.s.flush()
        return o

    @requires(SETTLE)
    async def settle_bulk(self, body) -> list[dict]:
        """Each order on its own (as Confirm delivery in bulk): one that cannot be settled does not stop the others."""
        out: list[dict] = []
        for oid in dict.fromkeys(body.order_ids):
            try:
                async with self.s.begin_nested():
                    await self._settle(await self._order(oid, ANY_VERSION), body)
                    await self.s.flush()
                out.append({"order_id": oid, "ok": True, "payment_status": SETTLED})
            except BusinessError as e:
                out.append({"order_id": oid, "ok": False, "error": e.message})
        return out
