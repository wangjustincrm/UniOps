"""Pipeline (design §13.11).

A cycle is a list of `Step`s; each unit of work inside a step (one
connection, one document, one order) goes through `isolated`, so one bad unit
is rolled back, logged and reported while the rest of the cycle carries on.

Transactions: `isolated` is the job's unit boundary — it opens the unit of
work and commits it once. The function it runs (and every service that
function calls) only flushes; it never commits (FR-OB-50, STEP 1).
"""
from __future__ import annotations

import logging
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.platform.actors import SYSTEM_ACTOR, Actor

logger = logging.getLogger(__name__)


@dataclass
class UnitOutcome:
    unit: str
    ok: bool
    result: Any = None
    error: BaseException | None = None
    error_type: str | None = None
    message: str | None = None
    trace: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    name: str
    outcomes: list[UnitOutcome] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


@dataclass
class CycleContext:
    run: Any  # the run record of the job driving the cycle
    actor: Actor = SYSTEM_ACTOR
    results: list[StepResult] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)  # handed from step to step

    @property
    def failed(self) -> int:
        return sum(r.failed for r in self.results)


class Step(Protocol):
    name: str

    async def run(self, ctx: CycleContext) -> StepResult: ...


ReportFailure = Callable[[UnitOutcome], Awaitable[None]]


async def isolated(
    unit_name: str,
    fn: Callable[[Any], Awaitable[Any]],
    *,
    uow_factory: Callable[..., Any],
    log: ReportFailure,
    actor: Actor = SYSTEM_ACTOR,
    context: dict[str, Any] | None = None,
) -> UnitOutcome:
    """Runs `fn(uow)` in its own unit of work and commits it. On error the
    unit rolls back and `log` is called with the outcome (the business code
    writes its log row and raises or refreshes the problem task there, in a
    unit of its own). Never re-raises an Exception; cancellation propagates."""
    ctx = dict(context or {})
    try:
        async with uow_factory(actor) as uow:
            result = await fn(uow)
            await uow.commit()
        return UnitOutcome(unit_name, True, result=result, context=ctx)
    except Exception as e:  # noqa: BLE001 — one bad unit must not stop the cycle
        outcome = UnitOutcome(
            unit_name, False, error=e, error_type=type(e).__name__, message=str(e),
            trace="".join(traceback.format_exception(e)), context=ctx,
        )
        try:
            await log(outcome)
        except Exception:  # noqa: BLE001
            logger.exception("could not record the failure of unit %s", unit_name)
        return outcome


async def run_steps(steps: Sequence[Step], ctx: CycleContext) -> CycleContext:
    for step in steps:
        ctx.results.append(await step.run(ctx))
    return ctx
