"""The transfer's record actions, one at a time (stage 3 c, h). Ready hands the 943 to the next pass (app/services/
transfers.py queue_ready); Resend queues a new one, once (app/services/queued.py); the others send nothing."""
from __future__ import annotations

from app.domain.status import TRANSFER
from app.domain.status import Transfer as T
from app.models.transfers import Transfer
from app.platform.bulk_actions import Action, ActionRegistry, refusal
from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError
from app.services.transfer_views import MANAGE, TransferRepository, TransferService, not_on_hold


async def ready_check(uow, t: Transfer) -> BusinessError | None:  # noqa: ARG001
    return None if t.status != T.READY_TO_SEND.value and TRANSFER.can(T(t.status), T.READY_TO_SEND) else BusinessError("transfer.not_draft", f"a transfer in {t.status} is not a draft to send")


async def ready_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.transfers.ready(uow, t.id, ANY_VERSION)  # locked, its version checked: by run_action


async def cancel_check(uow, t: Transfer, ctx) -> BusinessError | None:
    return await refusal(ctx.runtime.transfers.refuse_cancel(uow.session, t))


async def cancel_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.transfers.cancel(uow, t.id, ANY_VERSION, ctx.reason)


async def release_check(uow, t: Transfer) -> BusinessError | None:  # noqa: ARG001
    return not_on_hold(t)


async def release_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await TransferService(uow)._run_release(t, ctx.reason)


async def resend_check(uow, t: Transfer, ctx) -> BusinessError | None:
    return await refusal(ctx.runtime.transfers.refuse_resend(uow.session, t))


async def resend_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.transfers.resend(uow, t.id, ANY_VERSION)


TRANSFER_ACTIONS = ActionRegistry(TransferRepository, [
    Action("ready", label="Ready to send", permission=MANAGE, check=ready_check, run=ready_run, context=True, bulk=False,
           sends="the 943, on the next pass"),
    Action("resend", label="Resend 943", permission=MANAGE, check=resend_check, check_context=True, run=resend_run, context=True, bulk=False,
           sends="a new 943 (a new control number) to the warehouse"),
    Action("cancel", label="Cancel", permission=MANAGE, check=cancel_check, check_context=True, run=cancel_run, context=True, bulk=False, reason="required"),
    Action("release", label="Release hold", permission=MANAGE, check=release_check, run=release_run, context=True, bulk=False, reason="required"),
])
