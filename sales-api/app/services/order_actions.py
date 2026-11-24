"""The order's record actions (platform/bulk_actions.py; stage 3): one declaration each — the permission, the rule, the
work, one or many, a reason. The single routes call the same rule and the same work.

None of these sends a file or an email. Release hold may queue the Shopify write its hold had stopped (order_holds)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.order_rules import hold_exits
from app.domain.status import Fulfillment as F
from app.models.orders import Order
from app.platform.bulk_actions import Action, ActionRegistry
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


ORDER_ACTIONS = ActionRegistry(OrderRepository, [
    *PAYMENT_ACTIONS.actions.values(),
    Action("recheck", label="Re-check", permission=OPERATE, check=recheck_check, run=recheck_run, prepare=recheck_prepare, context=True),
    # One at a time (user 2026-11-24): each is a decision about one order.
    Action("cancel", label="Cancel order", permission=OPERATE, check=cancel_check, run=cancel_run, context=True, bulk=False, reason="required",
           confirm="It is Sales' own record: nothing is written to Shopify, the warehouse or a partner."),
    Action("release_hold", label="Release hold", permission=OPERATE, input=ReleaseTo, check=release_check, run=release_run, context=True, bulk=False,
           reason="required", sends="the Shopify write its hold stopped, when released to Shipped"),
    Action("submit_draft", label="Submit", permission=OPERATE, check=draft_check, run=submit_run, bulk=False),
    Action("cancel_draft", label="Cancel draft", permission=OPERATE, check=draft_check, run=cancel_draft_run, bulk=False),
])
