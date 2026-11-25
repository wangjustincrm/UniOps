"""What a session made in the transaction it is in: a record and its first parts are one making, over however many flushes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

_MADE = "_made_here"


def track(session: Session) -> None:
    if session.info.get("_made_tracked"):
        return
    session.info["_made_tracked"] = True
    event.listen(session, "after_flush", _after_flush)
    event.listen(session, "after_transaction_end", _after_transaction)


def made_here(session: Session, obj: Any) -> bool:
    return obj in session.new or inspect(obj).identity_key in session.info.get(_MADE, ())


def _after_flush(session: Session, flush_context: Any) -> None:  # noqa: ARG001
    session.info.setdefault(_MADE, set()).update(inspect(o).mapper.identity_key_from_instance(o) for o in session.new)


def _after_transaction(session: Session, transaction: Any) -> None:
    if transaction.parent is None:  # not a savepoint
        session.info.pop(_MADE, None)


__all__ = ["made_here", "track"]
