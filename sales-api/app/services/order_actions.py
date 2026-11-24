"""The order's record actions (platform/bulk_actions.py; stage 3): one declaration each — the permission, the rule, the
work, one or many, a reason. The single routes call the same rule and the same work.

Most send nothing. Confirm delivery sends the invoice too when asked (as the bulk confirmation always could); Release hold
may queue the Shopify write its hold had stopped (order_holds); Fulfil in Shopify now queues it (stage 3 g). What sends is
queued in the outbox, once (app/services/queued.py), one order at a time."""
from __future__ import annotations

from typing import Any

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.domain.order_rules import hold_exits
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.orders import Order
from app.platform.bulk_actions import Action, ActionRegistry, refusal
from app.platform.commands import ANY_VERSION
from app.services.invoice_views import MANAGE as INVOICE_MANAGE
from app.services.invoice_views import InvoiceService
from app.services.order_holds import OPERATE, OrderActions, cancel_refusal, shadow_refusal
from app.services.order_payment import ORDER_ACTIONS as PAYMENT_ACTIONS
from app.services.order_views import ManualOrderService, OrderRepository, OrderService
from app.services.orders import BarcodeLookup, OrderSync


async def cancel_check(uow, o: Order) -> str | None:  # noqa: ARG001
    return shadow_refusal(o) or cancel_refusal(o)


async def cancel_run(uow, o: Order, data, ctx) -> None:  # noqa: ARG001
    await OrderActions(uow, ctx.runtime.tasks)._run_cancel(o, ctx.reason)


class ReleaseTo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=1, max_length=30)


async def release_check(uow, o: Order) -> str | None:  # noqa: ARG001
    # What it may be released to depends on its 945s: the run decides. Here only whether it is held at all.
    held = hold_exits(o.fulfillment_status, o.fulfillment_status_before_hold, cancelled_at_source=o.source_cancelled_at is not None, applied_945=True)
    return shadow_refusal(o) or (None if held else f"not on hold (it is {o.fulfillment_status})")


async def release_run(uow, o: Order, data: ReleaseTo, ctx) -> None:
    await OrderActions(uow, ctx.runtime.tasks)._run_release(o, data.to, ctx.reason)


async def recheck_check(uow, o: Order) -> str | None:  # noqa: ARG001
    return None if o.fulfillment_status == F.REVIEW.value else "not in review"


async def recheck_prepare(uow, ids: list[int], ctx) -> dict[int, BarcodeLookup | None]:
    """The store's barcodes of every order in review, read before any order is locked: a slow store holds no row."""
    svc, out, stores = OrderService(uow), {}, {}
    for id in ids:
        o = await uow.session.get(Order, id)
        if o is None or o.fulfillment_status != F.REVIEW.value:
            continue
        key = (o.source_type, o.partner_code)
        if key not in stores:  # one lookup (and its cache) per store
            stores[key] = await svc._barcodes(o, ctx.runtime.container.shopify)
        await svc._read_barcodes(id, stores[key])
        out[id] = stores[key]
    return out


async def recheck_run(uow, o: Order, data, ctx) -> None:  # noqa: ARG001
    uow.set_reason("re-check review")
    remaining = await OrderSync(uow, ctx.runtime.intake.tasks).recheck(o, (ctx.prepared or {}).get(o.id))
    if remaining:
        ctx.note = "still in review: " + "; ".join(remaining)


def _draft(o: Order) -> str | None:
    if o.source_type != "MANUAL":
        return "not a manual order"
    return None if o.fulfillment_status == F.DRAFT.value else f"not a draft (it is {o.fulfillment_status})"


async def draft_check(uow, o: Order) -> str | None:  # noqa: ARG001
    return _draft(o)


async def submit_run(uow, o: Order, data: Any) -> None:  # noqa: ARG001
    await ManualOrderService(uow)._run_submit(o)


async def cancel_draft_run(uow, o: Order, data: Any) -> None:  # noqa: ARG001
    await ManualOrderService(uow)._run_cancel(o)


class ConfirmDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed_date: date | None = None  # none: each order on the date its delivery was due
    send: bool = False                  # and send each invoice that is then Ready, as the bulk confirmation always could


async def confirm_check(uow, o: Order) -> str | None:  # noqa: ARG001
    return shadow_refusal(o) or (None if o.invoice_status == Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value else
        f"the order's invoice is {o.invoice_status}, not waiting for delivery confirmation")


async def confirm_run(uow, o: Order, data: ConfirmDelivery, ctx) -> None:
    """FR-OB-31: in bulk, delivered = shipped less what is recorded as returned."""
    inv = await ctx.runtime.invoicing.confirm(uow, o.id, version=ANY_VERSION, confirmed_date=data.confirmed_date)  # run_action checked it
    if data.send and await InvoiceService(uow)._send_if_ready(ctx.runtime.invoicing, inv):
        ctx.note = "sent"


async def fulfil_check(uow, o: Order, ctx) -> str | None:
    return await refusal(ctx.runtime.shipments.refuse_fulfil(uow.session, o))


async def fulfil_run(uow, o: Order, data, ctx) -> None:  # noqa: ARG001
    uow.set_reason("Shopify: fulfil at the 997")
    await ctx.runtime.shipments.fulfil_at_997(uow, o.id, version=ANY_VERSION)  # run_action checked it


async def send_940_check(uow, o: Order, ctx) -> str | None:
    return await refusal(ctx.runtime.outbound.refuse_send(uow.session, o))


async def replace_940_check(uow, o: Order, ctx) -> str | None:
    return await refusal(ctx.runtime.outbound.refuse_send(uow.session, o, replace=True))


async def _send_940(uow, o: Order, ctx, *, replace: bool) -> None:
    doc = await OrderService(uow)._run_send_940(o.id, ctx.runtime.outbound, replace=replace, version=ANY_VERSION, reason=ctx.reason)  # run_action checked the version
    errors = (doc.body_json or {}).get("errors", [])
    ctx.note = f"the 940 failed its checks and was not sent: {'; '.join(errors)}" if errors else f"940 {doc.st02} queued"


async def send_940_run(uow, o: Order, data, ctx) -> None:  # noqa: ARG001
    await _send_940(uow, o, ctx, replace=False)


async def replace_940_run(uow, o: Order, data, ctx) -> None:  # noqa: ARG001
    await _send_940(uow, o, ctx, replace=True)


ORDER_ACTIONS = ActionRegistry(OrderRepository, [
    *PAYMENT_ACTIONS.actions.values(),
    Action("recheck", label="Re-check", permission=OPERATE, check=recheck_check, run=recheck_run, prepare=recheck_prepare, context=True),
    Action("confirm_delivery", label="Confirm delivery", permission=OPERATE, input=ConfirmDelivery, check=confirm_check, run=confirm_run,
           context=True, also=lambda d: [INVOICE_MANAGE] if d.send else [], sends="each invoice that is then Ready, when asked"),
    # One at a time (user 2026-11-24): each is a decision about one order.
    Action("cancel", label="Cancel order", permission=OPERATE, check=cancel_check, run=cancel_run, context=True, bulk=False, reason="required",
           confirm="It is Sales' own record: nothing is written to Shopify, the warehouse or a partner."),
    Action("release_hold", label="Release hold", permission=OPERATE, input=ReleaseTo, check=release_check, run=release_run, context=True, bulk=False,
           reason="required", sends="the Shopify write its hold stopped, when released to Shipped"),
    Action("fulfil_in_shopify", label="Fulfil in Shopify now", permission=OPERATE, check=fulfil_check, check_context=True, run=fulfil_run, context=True, bulk=False,
           sends="the fulfilment (or the archiving) of the order in Shopify"),
    Action("send_940", label="Send 940 now", permission=OPERATE, check=send_940_check, check_context=True, run=send_940_run, context=True, bulk=False,
           sends="a 940 to the warehouse"),
    Action("replace_940", label="Replace 940", permission=OPERATE, check=replace_940_check, check_context=True, run=replace_940_run, context=True,
           bulk=False, reason="required", sends="a new 940 to the warehouse, in place of the live one",
           confirm="The live 940 is marked Replaced and a new 940 with a new control number is sent. The warehouse then holds two 940s for "
                   "this order: make sure they cancel the first."),
    Action("submit_draft", label="Submit", permission=OPERATE, check=draft_check, run=submit_run, bulk=False),
    Action("cancel_draft", label="Cancel draft", permission=OPERATE, check=draft_check, run=cancel_draft_run, bulk=False),
])
