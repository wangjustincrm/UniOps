"""Who is making the change, available where a request is not.

The change log is written from a SQLAlchemy flush hook, which sees the session
and not the request. Passing the user down through every crud function would be
the same "one line per write path" problem the hook exists to avoid — and the
line would be missing from whichever path was written next.

Best effort on purpose. A change with no actor is still recorded, with the
actor blank; dropping the row instead would put holes in a trail that is meant
to be read as complete.
"""
from __future__ import annotations

import contextvars
import uuid

_actor_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "ehs_actor_id", default=None)
_actor_name: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ehs_actor_name", default=None)


def set_actor(user_id: uuid.UUID | None, name: str | None = None) -> None:
    _actor_id.set(user_id)
    _actor_name.set(name)


def current_actor() -> tuple[uuid.UUID | None, str | None]:
    return _actor_id.get(), _actor_name.get()
