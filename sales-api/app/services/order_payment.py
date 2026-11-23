"""Payment: a finance user marks an order Settled, or undoes the mark (user 2026-11-19 / 2026-11-23).

"For now it is manual with a finance user", for every source (retailer, manual, Shopify): the date the money came and
a reference (bank / cheque / remittance no.) are required, an amount and a note are not. Who marked it and when are
kept on the order; the mark and its undo (with a reason) are in the order's Activity (the audit, per order also in
bulk). Nothing is sent to anyone and the order's state does not change (Completed does not wait for Settled: the
user has not decided).

sales.settle only — a finance key, not on sales.read or sales.order.operate. The rules (`settle_check`,
`unsettle_check`) and the work (`settle_run`, `unsettle_run`) are one each, used by the single endpoints and by the
record actions (ORDER_ACTIONS, platform/bulk_actions.py): a draft, a shadow order, one already settled, or one whose
Payment is Not needed (cancelled with nothing invoiced, or historical — uniops-3d 2026-11-23, easy to reverse here)
is not settled. A later derived source (Shopify from the gateway settlements) goes beside the manual columns; the
manual mark wins (app/domain/order_payment.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.domain.order_payment import NOT_NEEDED
from app.domain.schedule import TORONTO
from app.domain.shadow import TEST_RUN_REFUSAL
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.platform.authorization import requires
from app.platform.bulk_actions import ActionRegistry, BulkAction
from app.platform.commands import ANY_VERSION, VersionArg, check_version, load_for_command
from app.platform.errors import BusinessError, InvalidInput
from app.platform.services import Service
from app.schemas.orders import SettleIn, UnsettleIn
from app.services import oplog

SETTLE = "sales.settle"
STEP = "payment"


async def _payment(uow, o: Order) -> str | None:
    return (await uow.session.execute(select(Order.payment_status_sql).where(Order.id == o.id))).scalar()


async def settle_check(uow, o: Order) -> str | None:
    """Why this order is not settled; None: it can be."""
    if o.shadow:
        return TEST_RUN_REFUSAL
    if o.fulfillment_status == F.DRAFT.value:
        return "a draft is not an order yet: submit it first"
    if o.settled_on is not None:
        return f"already settled on {o.settled_on:%Y-%m-%d} ({o.settled_reference})"
    if await _payment(uow, o) == NOT_NEEDED:
        return "no payment is expected (Not needed: cancelled with nothing invoiced, or historical)"
    return None


async def unsettle_check(uow, o: Order) -> str | None:  # noqa: ARG001
    if o.shadow:
        return TEST_RUN_REFUSAL
    return None if o.settled_on is not None else "not settled"


async def settle_run(uow, o: Order, body: SettleIn) -> None:
    if body.settled_on > datetime.now(TORONTO).date():
        raise InvalidInput("order.settled_future", "the settled date cannot be in the future", {"field": "settled_on"})
    actor = uow.actor
    o.settled_on, o.settled_reference, o.settled_amount = body.settled_on, body.reference, body.amount
    o.settled_note = body.note or None
    o.settled_at, o.settled_by, o.settled_by_name = datetime.now(timezone.utc), actor.user_id, actor.name
    what = f"settled on {body.settled_on:%Y-%m-%d}, {body.reference}" + (f", {body.amount:.2f}" if body.amount is not None else "")
    uow.set_reason(what + (f": {body.note}" if body.note else ""))
    await oplog.write(uow.session, "INFO", STEP, f"order {o.label} {what}", order_id=o.id)
    await uow.session.flush()


async def unsettle_run(uow, o: Order, body: UnsettleIn) -> None:
    was = f"{o.settled_on:%Y-%m-%d}, {o.settled_reference}"
    o.settled_on = o.settled_reference = o.settled_amount = o.settled_note = None
    o.settled_at = o.settled_by = o.settled_by_name = None
    uow.set_reason(f"settlement undone (was {was}): {body.reason}")
    await oplog.write(uow.session, "INFO", STEP, f"order {o.label}: settlement undone (was {was}): {body.reason}", order_id=o.id)
    await uow.session.flush()


def _order_actions() -> ActionRegistry:
    from app.services.order_views import OrderRepository  # order_views imports this module's neighbours
    return ActionRegistry(OrderRepository, [
        BulkAction("settle", permission=SETTLE, input=SettleIn, check=settle_check, run=settle_run),
        BulkAction("unsettle", permission=SETTLE, input=UnsettleIn, check=unsettle_check, run=unsettle_run),
    ])


ORDER_ACTIONS = _order_actions()


class OrderPaymentService(Service):
    """The single-record endpoints: the version the person saw, then the same rule and work as the actions."""

    async def _one(self, id: int, version: VersionArg, check, run, body) -> Order:
        o = await load_for_command(self.s, Order, id, version=ANY_VERSION, what="this order")
        check_version(o, version, "this order")
        why = await check(self.uow, o)
        if why:
            code = ("order.not_settled" if check is unsettle_check else
                    "order.settled" if o.settled_on is not None else "order.not_settleable")
            raise BusinessError(code, why)
        await run(self.uow, o, body)
        return o

    @requires(SETTLE)
    async def settle(self, id: int, body: SettleIn, version: VersionArg = ANY_VERSION) -> Order:
        return await self._one(id, version, settle_check, settle_run, body)

    @requires(SETTLE)
    async def unsettle(self, id: int, reason: str, version: VersionArg = ANY_VERSION) -> Order:
        return await self._one(id, version, unsettle_check, unsettle_run, UnsettleIn(reason=reason))
