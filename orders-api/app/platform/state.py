"""State machine (design §13.6).

The business code declares each machine from its allowed-transition table:

    FULFILLMENT = StateMachine({Draft: {Review, Pending, Cancelled}, ...}, name="fulfillment")
    FULFILLMENT.transition(order, Pending, actor=actor, events=uow.events)

`transition` refuses anything not in the table, sets the field (the audit
trail records old/new on flush) and raises a StatusChanged event.

Hold states: a state "the entity returns from to the status it held before"
is declared in `holds` with the states that may enter it. Entering stores the
previous status in `<field>_before_hold` (the entity needs that column);
leaving is allowed to that remembered status or to the hold state's explicit
targets, and clears it.
"""
from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping
from typing import Any, Generic, TypeVar

from app.platform.actors import Actor
from app.platform.errors import BusinessError
from app.platform.events import EventBuffer, StatusChanged

S = TypeVar("S", bound=Hashable)


class StateMachine(Generic[S]):
    def __init__(
        self,
        transitions: Mapping[S, Iterable[S]],
        name: str,
        *,
        field: str = "status",
        initial: Iterable[S] = (),
        holds: Mapping[S, Iterable[S]] | None = None,
    ) -> None:
        self.name = name
        self.field = field
        self.memory_field = f"{field}_before_hold"
        self.transitions: dict[S, frozenset[S]] = {s: frozenset(d) for s, d in transitions.items()}
        self.initial = frozenset(initial)
        self.holds: dict[S, frozenset[S]] = {h: frozenset(src) for h, src in (holds or {}).items()}
        states = set(self.transitions)
        unknown = {d for ds in self.transitions.values() for d in ds} - states
        unknown |= set(self.initial) - states
        unknown |= set(self.holds) - states
        unknown |= {s for srcs in self.holds.values() for s in srcs} - states
        if unknown:
            raise ValueError(f"{name}: states without a row in the table: {sorted(map(str, unknown))}")

    @property
    def states(self) -> frozenset[S]:
        return frozenset(self.transitions)

    def can(self, src: S, dst: S, *, held_before: S | None = None) -> bool:
        if dst in self.transitions.get(src, frozenset()):
            return True
        if src in self.holds.get(dst, frozenset()):
            return True
        return src in self.holds and held_before is not None and dst == held_before

    def pairs(self) -> set[tuple[S, S]]:
        """Every statically allowed pair: the table plus hold entries. Returns
        from a hold state to its remembered status are dynamic and not listed."""
        out = {(s, d) for s, ds in self.transitions.items() for d in ds}
        out |= {(s, h) for h, srcs in self.holds.items() for s in srcs}
        return out

    def transition(
        self,
        entity: Any,
        dst: S,
        *,
        actor: Actor,
        reason: str | None = None,
        events: EventBuffer | None = None,
    ) -> StatusChanged:
        src = getattr(entity, self.field)
        held_before = getattr(entity, self.memory_field, None) if src in self.holds else None
        if not self.can(src, dst, held_before=held_before):
            raise BusinessError(
                "state.invalid_transition",
                f"{self.name}: {_label(src)} → {_label(dst)} is not allowed",
                {"machine": self.name, "from": _label(src), "to": _label(dst)},
            )
        if dst in self.holds and src in self.holds[dst]:
            setattr(entity, self.memory_field, src)
        elif src in self.holds:
            setattr(entity, self.memory_field, None)
        setattr(entity, self.field, dst)
        ev = StatusChanged(
            actor=actor,
            machine=self.name,
            entity=getattr(entity, "__tablename__", type(entity).__name__),
            entity_id=getattr(entity, "id", None),
            old=src,
            new=dst,
            reason=reason,
        )
        if events is not None:
            events.raise_(ev)
        return ev


def _label(s: Any) -> str:
    return str(getattr(s, "value", s))
