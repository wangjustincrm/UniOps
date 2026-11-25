"""Which rows a session holds a lock on, as the tests see it: every FOR UPDATE load (get, select, refresh) goes through
do_orm_execute, and a row this transaction updated is locked too. platform/parts asks it before a part is written: a part written
while its root is not locked fails the test, for the code under app/. What a test writes by itself (its seeds) is not asked."""
from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.platform import parts

HELD = "_rows_held"


def _key(obj: Any) -> Any:
    return inspect(obj).mapper.identity_key_from_instance(obj)


def _on_execute(state: Any) -> Any:
    if not state.is_select or getattr(state.statement, "_for_update_arg", None) is None or state.execution_options.get(parts.OWN):
        return None
    frozen = state.invoke_statement().freeze()
    held = state.session.info.setdefault(HELD, set())
    for row in frozen().all():
        held.update(_key(x) for x in (row if isinstance(row, tuple) or hasattr(row, "_mapping") else (row,)) if hasattr(x, "_sa_instance_state"))
    return frozen()


def _on_flush(session: Any, ctx: Any) -> None:  # noqa: ARG001
    session.info.setdefault(HELD, set()).update(_key(o) for o in session.dirty if inspect(o).session is not None)


def _on_end(session: Any, transaction: Any) -> None:
    if transaction.parent is None:
        session.info.pop(HELD, None)


class NotLocked(AssertionError):
    pass


def check(session: Any, root: Any) -> None:
    if _key(root) not in session.info.get(HELD, set()):
        raise NotLocked(f"a part of {type(root).__name__} {inspect(root).identity} is written and the unit holds no lock on it: lock the root first")


def install() -> None:
    event.listen(Session, "do_orm_execute", _on_execute)
    event.listen(Session, "after_flush", _on_flush)
    event.listen(Session, "after_transaction_end", _on_end)
    parts.check_locked = check
