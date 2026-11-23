"""The correlation id (≈ ABP's ICorrelationIdProvider + AbpCorrelationIdOptions, user 2026-11-23): one id per logical
operation — a request, a job run, an outbox send — read from the caller's header (X-Correlation-Id, X-Request-Id
accepted), else new; returned in the response, carried by the audit log, the oplog context, a 500's body and, later,
the outbox and outgoing calls. Ambient (a contextvar), so nothing passes it by hand.
"""
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.platform.options import CorrelationOptions, options

_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_current: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current() -> str | None:
    return _current.get()


def new_id() -> str:
    return uuid.uuid4().hex


def valid(value: str | None) -> bool:
    return bool(value) and bool(_TOKEN.match(value or ""))


def from_headers(headers: list[tuple[bytes, bytes]]) -> str:
    """The caller's id when it is a plain token (the accepted headers in order), else a new one."""
    wanted = [h.lower().encode() for h in options(CorrelationOptions).accepted_headers]
    found = {k.lower(): v.decode("latin-1") for k, v in headers if k.lower() in wanted}
    for name in wanted:
        if valid(found.get(name)):
            return found[name]
    return new_id()


def response_headers(cid: str) -> list[tuple[bytes, bytes]]:
    return [(h.lower().encode(), cid.encode()) for h in options(CorrelationOptions).response_headers]


def set_current(cid: str):
    """Make `cid` the ambient id; returns the token to reset it."""
    return _current.set(cid)


@contextmanager
def use(cid: str | None = None) -> Iterator[str]:
    """Run a block under `cid` (or the ambient one, or a new one)."""
    cid = cid or _current.get() or new_id()
    token = _current.set(cid)
    try:
        yield cid
    finally:
        _current.reset(token)
