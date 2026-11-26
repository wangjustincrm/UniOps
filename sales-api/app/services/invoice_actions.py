"""The invoice's record actions (stage 3 j): Send — one or the selected ones (user 2026-11-26, future item 18) — and Send again,
one at a time (it says why); each queued once (app/services/queued.py). Every path locks the order, then its invoice: so do these
(lock_first). A Send that fits says how it goes (its note: an 810 file to TrueCommerce, or an email to the partner's address),
in the dry run too."""
from __future__ import annotations

from sqlalchemy import select

from app.models.config import Partner
from app.models.invoices import Invoice
from app.models.orders import Order
from app.platform.bulk_actions import Action, ActionRegistry, refusal
from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError
from app.services.invoice_views import MANAGE, InvoiceRepository

SENDS = "the invoice to the partner (an 810 or an email), and the company's copy"


async def lock_order(uow, id: int) -> None:
    order_id = (await uow.session.execute(select(Invoice.order_id).where(Invoice.id == id))).scalar_one_or_none()
    if order_id is not None:
        await uow.session.get(Order, order_id, with_for_update=True, populate_existing=True)


async def _check(uow, inv: Invoice, ctx, *, again: bool) -> BusinessError | None:
    order = await uow.session.get(Order, inv.order_id)  # locked by lock_order
    why = await refusal(ctx.runtime.invoicing.refuse_send(uow.session, order, inv, again=again))
    if why is None:
        ctx.note = await way(uow.session, order, inv)
    return why


async def way(s, order: Order, inv: Invoice) -> str:
    """How it goes: "an 810 file to TrueCommerce", "an email to ap@partner.example"."""
    if inv.method == "EMAIL":
        address = (await s.execute(select(Partner.invoice_email).where(Partner.code == order.partner_code))).scalar_one_or_none()
        return f"an email to {address or 'the partner (no address set)'}"
    return "an 810 file to TrueCommerce" if inv.method == "810_JSON" else f"by {inv.method}"


async def send_check(uow, inv: Invoice, ctx) -> BusinessError | None:
    return await _check(uow, inv, ctx, again=False)


async def send_again_check(uow, inv: Invoice, ctx) -> BusinessError | None:
    return await _check(uow, inv, ctx, again=True)


async def send_run(uow, inv: Invoice, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.invoicing.send(uow, inv.id, version=ANY_VERSION)  # run_action checked it


async def send_again_run(uow, inv: Invoice, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.invoicing.send(uow, inv.id, version=ANY_VERSION, again=True, reason=ctx.reason)


INVOICE_ACTIONS = ActionRegistry(InvoiceRepository, [
    Action("send", label="Send", permission=MANAGE, check=send_check, check_context=True, run=send_run, context=True, sends=SENDS),
    Action("send_again", label="Send again", permission=MANAGE, check=send_again_check, check_context=True, run=send_again_run, context=True,
           bulk=False, reason="required", sends=SENDS, confirm="The partner has this invoice: it gets it a second time."),
], lock_first=lock_order)
