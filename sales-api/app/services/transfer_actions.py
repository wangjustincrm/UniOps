"""The transfer's record actions (stage 3 c): what sends nothing itself, one at a time. Ready hands the 943 to the next pass
(app/services/transfers.py queue_ready), as before; the resend of a 943 is not here."""
from __future__ import annotations

from app.domain.status import TRANSFER
from app.domain.status import Transfer as T
from app.models.transfers import Transfer
from app.platform.bulk_actions import Action, ActionRegistry
from app.platform.commands import ANY_VERSION
from app.services.transfer_views import MANAGE, TransferRepository, TransferService, not_on_hold
from app.services.transfers import CANCELLABLE


async def ready_check(uow, t: Transfer) -> str | None:  # noqa: ARG001
    return None if t.status != T.READY_TO_SEND.value and TRANSFER.can(T(t.status), T.READY_TO_SEND) else f"a transfer in {t.status} is not a draft to send"


async def ready_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.transfers.ready(uow, t.id, ANY_VERSION)  # locked, its version checked: by run_action


async def cancel_check(uow, t: Transfer) -> str | None:  # noqa: ARG001
    return None if T(t.status) in CANCELLABLE else f"a transfer in {t.status} cannot be cancelled: the 943 is sent"


async def cancel_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await ctx.runtime.transfers.cancel(uow, t.id, ANY_VERSION, ctx.reason)


async def release_check(uow, t: Transfer) -> str | None:  # noqa: ARG001
    return not_on_hold(t)


async def release_run(uow, t: Transfer, data, ctx) -> None:  # noqa: ARG001
    await TransferService(uow)._run_release(t, ctx.reason)


TRANSFER_ACTIONS = ActionRegistry(TransferRepository, [
    Action("ready", label="Ready to send", permission=MANAGE, check=ready_check, run=ready_run, context=True, bulk=False,
           sends="the 943, on the next pass"),
    Action("cancel", label="Cancel", permission=MANAGE, check=cancel_check, run=cancel_run, context=True, bulk=False, reason="required"),
    Action("release", label="Release hold", permission=MANAGE, check=release_check, run=release_run, context=True, bulk=False, reason="required"),
])
