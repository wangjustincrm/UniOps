"""A Radiant report's record actions (stage 3 e): apply it as the start, or undo that with why. Nothing is sent."""
from __future__ import annotations

from app.models.stock import StockCount
from app.platform.bulk_actions import Action, ActionRegistry
from app.services.stock_counts import ADJUST, StockCountRepository, StockCountService


async def apply_check(uow, c: StockCount) -> str | None:  # noqa: ARG001
    return None if c.applied_at is None else "this count is already the start"


async def apply_run(uow, c: StockCount, data) -> None:  # noqa: ARG001
    await StockCountService(uow)._run_apply(c)


async def undo_check(uow, c: StockCount) -> str | None:  # noqa: ARG001
    return None if c.applied_at is not None else "this count is not applied"


async def undo_run(uow, c: StockCount, data, ctx) -> None:  # noqa: ARG001
    await StockCountService(uow)._run_undo(c, ctx.reason)


COUNT_ACTIONS = ActionRegistry(StockCountRepository, [
    Action("apply", label="Apply", permission=ADJUST, check=apply_check, run=apply_run, bulk=False),
    Action("undo", label="Undo", permission=ADJUST, check=undo_check, run=undo_run, context=True, bulk=False, reason="required"),
])
