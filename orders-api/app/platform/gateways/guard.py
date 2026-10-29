"""The guard on every outbound write (design §5.5 / §13.9).

`GuardedGateway` wraps a gateway and is the only place that reads the
outbound switches. It exposes exactly two kinds of method, both named by the
composition root:

- `guarded` — a write: it runs only when its switch is true in the settings at
  call time and the optional `allow` check passes (e.g. the connection is
  LIVE). Otherwise it is refused: logged as "would have sent/written",
  reported to `on_refused` (the business code writes its log row there), and
  `Suppressed` is raised, so the caller can never mistake it for a send.
- `reads` — passed through.

Anything else — another method, a private attribute, the wrapped client — is
an AttributeError. The wrapped object is held only in closures.

The switch names come from the composition root; nothing here knows them.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Refused:
    gateway: str
    method: str
    switch: str
    reason: str  # "switch off" | the allow check's reason
    summary: dict[str, Any]


class Suppressed(Exception):
    """A write the guard refused. Nothing left the building; the outbox marks
    the row SUPPRESSED (product owner, 2026-10-28) and a person decides."""

    def __init__(self, refused: Refused) -> None:
        super().__init__(f"{refused.gateway}.{refused.method} suppressed ({refused.switch}: {refused.reason})")
        self.refused = refused


Allow = Callable[[str, tuple, dict], str | None]  # → None to allow, or the refusal reason
Summarise = Callable[[str, tuple, dict], dict[str, Any]]


def _default_summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:  # noqa: ARG001
    def short(v: Any) -> Any:
        if isinstance(v, (bytes, bytearray)):
            return f"<{len(v)} bytes>"
        s = repr(v)
        return s if len(s) <= 200 else s[:200] + "…"
    return {"args": [short(a) for a in args], "kwargs": {k: short(v) for k, v in kwargs.items()}}


class GuardedGateway:
    def __init__(
        self,
        inner: Any,
        *,
        settings: Any,
        guarded: Mapping[str, str],  # method name → switch (settings attribute) name
        reads: Iterable[str] = (),
        name: str | None = None,
        allow: Allow | None = None,
        summarise: Summarise = _default_summary,
        on_refused: Callable[[Refused], Awaitable[None] | None] | None = None,
    ) -> None:
        reads = frozenset(reads)
        both = reads & set(guarded)
        if both:
            raise TypeError(f"method(s) {sorted(both)} are both guarded and reads")
        missing = [m for m in [*guarded, *reads] if m.startswith("_") or not callable(getattr(inner, m, None))]
        if missing:
            raise TypeError(f"{type(inner).__name__} has no public method(s) {missing}")
        gw_name = name or type(inner).__name__
        methods: dict[str, Callable[..., Any]] = {}
        for m in reads:
            methods[m] = _passthrough(getattr(inner, m), m)
        for m, switch in guarded.items():
            methods[m] = _guarded(self, getattr(inner, m), gw_name, m, switch, settings, allow, summarise, on_refused)
        object.__setattr__(self, "_methods", methods)
        object.__setattr__(self, "name", gw_name)
        object.__setattr__(self, "refused", [])

    def __getattribute__(self, attr: str) -> Any:
        methods = object.__getattribute__(self, "_methods")
        if attr in methods:
            return methods[attr]
        if attr in ("name", "refused", "__class__", "__repr__", "__dir__"):
            return object.__getattribute__(self, attr)
        raise AttributeError(f"{object.__getattribute__(self, 'name')}: {attr!r} is not an allowed method")

    def __setattr__(self, attr: str, value: Any) -> None:
        raise AttributeError("a guarded gateway is read-only")

    def __dir__(self) -> list[str]:
        return sorted(object.__getattribute__(self, "_methods"))

    def __repr__(self) -> str:
        return f"<GuardedGateway {object.__getattribute__(self, 'name')}>"


def _passthrough(target: Callable[..., Any], method: str) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return target(*args, **kwargs)
    call.__name__ = method
    return call


def _guarded(gw, target, gw_name, method, switch, settings, allow, summarise, on_refused) -> Callable[..., Any]:
    async def call(*args: Any, **kwargs: Any) -> Any:
        reason = None
        if getattr(settings, switch, False) is not True:
            reason = "switch off"
        elif allow is not None:
            reason = allow(method, args, kwargs)
        if reason is None:
            return await target(*args, **kwargs)
        refused = Refused(gw_name, method, switch, reason, summarise(method, args, kwargs))
        gw.refused.append(refused)
        logger.warning("would have called %s.%s (%s: %s) %s", gw_name, method, switch, reason, refused.summary)
        if on_refused is not None:
            result = on_refused(refused)
            if inspect.isawaitable(result):
                await result
        raise Suppressed(refused)
    call.__name__ = method
    return call
