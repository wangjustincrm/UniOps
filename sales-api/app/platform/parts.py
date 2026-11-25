"""A record's parts move its version (≈ ABP: the aggregate root is updated when its children change).

A part says whose it is, once, on its model:

    class OrderLine(Audited, Base):
        __part_of__ = PartOf(Order, "order_id")

Made, changed in a column of its own (by its real history: a value set to what it was is no change; the bookkeeping columns
do not count) or deleted, it moves the root's version and its updated_at / by / name with the same flush: whoever saw the
record before is refused (entity.stale), as for a change of the record itself. No command does this by hand. `touch`
(commands.py) is for a press that changes nothing yet.

- Once per root and flush. A root made in the same transaction stays at the version it was made with.
- Moved to another root, a part moves both.
- The root is locked before its parts are written (the same order everywhere, else two units wait for each other). Not loaded,
  it is loaded and locked here, so it is moved all the same; the tests refuse a part written before its root's lock.
- Bulk SQL (update(), delete()) goes round this: not for a part (tests/test_parts.py).
- No audit row of its own: the parts have theirs.

The root of a person's request that someone else moved meanwhile: StaleDataError, answered 409 entity.stale (errors.py)."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.platform.audit import changed_columns
from app.platform.clock import clock

_MADE = "_roots_made"
OWN = "root_of_a_part"  # marks the lock this module takes itself: late, not the writer's


@dataclass(frozen=True)
class PartOf:
    root: type
    fk: str          # the part's attribute that names its root
    key: str = "id"  # the root's attribute it names


def part_of(obj: Any) -> PartOf | None:
    return getattr(type(obj), "__part_of__", None)


# The tests' check: called with (session, root) for a root whose part is written; raises when this unit holds no lock on it.
check_locked: Callable[[Session, Any], None] | None = None


class RootVersions:
    """Attached to a session before its AuditTrail, whose listener then sees the root as changed."""

    def attach(self, session: Session) -> None:
        if session.info.get("_parts_attached"):
            return
        session.info["_parts_attached"] = True
        event.listen(session, "before_flush", _before_flush)
        event.listen(session, "after_flush", _after_flush)
        event.listen(session, "after_transaction_end", _after_transaction)


def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:  # noqa: ARG001
    roots: dict[tuple[type, Any], PartOf] = {}
    for obj, p in _written(session):
        for value in _named(obj, p):
            if value is None:
                raise RuntimeError(f"a {type(obj).__name__} without its {p.root.__name__} ({p.fk})")
            roots.setdefault((p.root, value), p)
    made = session.info.get(_MADE, set())
    for (cls, value), p in roots.items():
        root = _root(session, p, value)
        if root is None:
            continue  # gone: the part's own key is refused by the database
        if root in session.new or inspect(root).identity_key in made:
            continue
        if check_locked is not None:
            check_locked(session, root)
        actor = session.info.get("actor")
        root.updated_at = clock().now()
        if actor is not None:
            root.updated_by, root.updated_by_name = actor.user_id, actor.name
        flag_modified(root, "updated_at")  # also in the same instant by the same person: the version moves


def _written(session: Session) -> Iterable[tuple[Any, PartOf]]:
    for obj in [*session.new, *session.deleted]:
        if (p := part_of(obj)) is not None:
            yield obj, p
    for obj in list(session.dirty):
        if (p := part_of(obj)) is not None and changed_columns(obj):
            yield obj, p


def _named(obj: Any, p: PartOf) -> list[Any]:
    """The root(s) it names: the one it has and, moved, the one it had."""
    h = inspect(obj).attrs[p.fk].history
    return list(dict.fromkeys([*h.added, *h.unchanged, *(h.deleted or ())])) or [getattr(obj, p.fk)]


def _root(session: Session, p: PartOf, value: Any) -> Any:
    for obj in session.new:
        if isinstance(obj, p.root) and getattr(obj, p.key) == value:
            return obj
    if p.key == "id":
        here = session.identity_map.get(session.identity_key(p.root, value))
    else:
        here = next((x for x in session.identity_map.values() if isinstance(x, p.root) and x.__dict__.get(p.key) == value), None)
    if here is not None and not inspect(here).expired:
        return here
    return session.execute(select(p.root).where(getattr(p.root, p.key) == value).with_for_update(of=p.root),
                           execution_options={OWN: True}).scalars().one_or_none()


def _after_flush(session: Session, flush_context: Any) -> None:  # noqa: ARG001
    made = session.info.setdefault(_MADE, set())
    made.update(inspect(o).mapper.identity_key_from_instance(o) for o in session.new)


def _after_transaction(session: Session, transaction: Any) -> None:
    if transaction.parent is None:
        session.info.pop(_MADE, None)


__all__ = ["PartOf", "RootVersions", "part_of"]
