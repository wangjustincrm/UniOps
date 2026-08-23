"""Registry for fire-and-forget asyncio tasks.

`asyncio.create_task(...)` without keeping the returned Task has two failure
modes, and this codebase hit both:

1. **The task can vanish mid-flight.** The event loop only holds a *weak*
   reference, so a task nobody references may be garbage-collected before it
   finishes — the interpreter's "Task was destroyed but it is pending!" warning.

2. **Nothing drains them at shutdown.** Every fire-and-forget coroutine here
   opens its own `async with AsyncSessionLocal() as db`. If the loop stops
   while the task is suspended inside that block, `__aexit__` never runs, so
   the session is never closed and its connection goes back to the pool still
   holding an open transaction. Observed in the epms-api suite: a leaked
   `idle in transaction` backend held a lock on `users` for eight minutes and
   deadlocked the next test module's `DROP TABLE` teardown ("coroutine
   'AsyncSession.close' was never awaited" in the same run).

`spawn()` fixes (1) by keeping a strong reference until the task completes.
`drain()` fixes (2): the app lifespan awaits in-flight work briefly and then
cancels the stragglers, and the test suite cancels immediately between tests.
Cancellation is what makes this correct — `CancelledError` derives from
BaseException, so it is NOT swallowed by the `except Exception` guards inside
these coroutines; it propagates through the `async with`, whose `__aexit__`
closes the session properly.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Strong references to in-flight fire-and-forget tasks. Entries remove
# themselves via the done-callback, so this never grows unbounded.
_BACKGROUND: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _BACKGROUND.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # These coroutines are already expected to swallow their own errors;
        # anything reaching here escaped that, and would otherwise be silent.
        logger.error("Background task %s failed: %s", task.get_name(), exc)


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task | None:
    """Schedule `coro` as a tracked background task.

    Returns None (and closes the coroutine, so it does not emit a "never
    awaited" RuntimeWarning) when there is no running loop — a sync caller.
    """
    try:
        task = asyncio.create_task(coro, name=name)
    except RuntimeError:  # no running event loop
        coro.close()
        logger.warning("No running event loop; dropped background task %s", name)
        return None
    _BACKGROUND.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_count() -> int:
    return sum(1 for t in _BACKGROUND if not t.done())


async def drain(timeout: float = 10.0) -> int:
    """Settle every in-flight background task; return how many were waited on.

    Waits up to `timeout` seconds for them to finish on their own, then cancels
    whatever is left and awaits the cancellation so each `async with` unwinds
    and releases its DB connection. `timeout=0` skips the grace period and
    cancels immediately (what the test suite wants — it does not care whether
    the email went out, only that no connection is left mid-transaction).

    Safe to call when nothing is pending, and safe to call repeatedly.
    """
    pending = [t for t in _BACKGROUND if not t.done()]
    if not pending:
        return 0
    if timeout > 0:
        _, still_running = await asyncio.wait(pending, timeout=timeout)
    else:
        still_running = set(pending)
    for task in still_running:
        task.cancel()
    if still_running:
        # Must await, or the cancellation never actually unwinds the coroutine
        # and the session stays open — the very bug this module exists for.
        await asyncio.gather(*still_running, return_exceptions=True)
    return len(pending)
