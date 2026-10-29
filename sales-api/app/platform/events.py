"""Domain events (design §13.7).

Events raised during a unit of work are collected in its `EventBuffer`.
Internal handlers (close a fulfilment, open or complete a task, recompute a
worklist entry) run inside the same transaction just before COMMIT, so their
writes commit or roll back together with the change that raised the event.
External effects never run here — they are outbox rows (outbox.py).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.platform.actors import Actor


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    actor: Actor
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, kw_only=True)
class StatusChanged(DomainEvent):
    machine: str
    entity: str
    entity_id: Any
    old: Any
    new: Any
    reason: str | None = None


Handler = Callable[[Any, Any], Awaitable[None]]  # (event, uow)

# A handler that keeps raising events which trigger itself would never let
# the transaction commit.
MAX_DISPATCH_ROUNDS = 10


class EventBuffer:
    def __init__(self) -> None:
        self._pending: list[DomainEvent] = []
        self.dispatched: list[DomainEvent] = []

    def raise_(self, event: DomainEvent) -> None:
        self._pending.append(event)

    def drain(self) -> list[DomainEvent]:
        out, self._pending = self._pending, []
        return out

    def __len__(self) -> int:
        return len(self._pending)


class HandlerRegistry:
    """Event type → in-transaction handlers, run in registration order.
    A handler registered for a base class also sees its subclasses."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def on(self, event_type: type[DomainEvent], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def handlers_for(self, event: DomainEvent) -> list[Handler]:
        out: list[Handler] = []
        for cls in type(event).__mro__:
            out.extend(self._handlers.get(cls, ()))
        return out

    async def dispatch(self, buffer: EventBuffer, uow: Any) -> None:
        """Runs handlers until no event is pending; events raised by a handler
        are dispatched in the next round."""
        for _ in range(MAX_DISPATCH_ROUNDS):
            events = buffer.drain()
            if not events:
                return
            for event in events:
                for handler in self.handlers_for(event):
                    await handler(event, uow)
                buffer.dispatched.append(event)
        raise RuntimeError(f"events still pending after {MAX_DISPATCH_ROUNDS} dispatch rounds")
