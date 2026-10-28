"""The guard on every outbound write (design §5.5 / §13.9).

`GuardedGateway` wraps a gateway and is the ONLY place that reads the
outbound switches. A guarded method runs only when its switch is true in the
settings at call time and the optional `allow` check passes (e.g. the
connection is LIVE); otherwise the call is refused: logged as "would have
sent/written", reported to `on_refused`, and returns None without effect.
Unguarded methods (reads) pass straight through.

The switch names come from the composition root; nothing here knows them.
"""
from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
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
        name: str | None = None,
        allow: Allow | None = None,
        summarise: Summarise = _default_summary,
        on_refused: Callable[[Refused], Awaitable[None] | None] | None = None,
    ) -> None:
        missing = [m for m in guarded if not callable(getattr(inner, m, None))]
        if missing:
            raise TypeError(f"{type(inner).__name__} has no method(s) {missing}")
        self._inner = inner
        self._settings = settings
        self._guarded = dict(guarded)
        self._name = name or type(inner).__name__
        self._allow = allow
        self._summarise = summarise
        self._on_refused = on_refused
        self.refused: list[Refused] = []

    def __getattr__(self, attr: str) -> Any:
        target = getattr(self._inner, attr)
        switch = self._guarded.get(attr)
        if switch is None:
            return target

        @functools.wraps(target)
        async def guarded_call(*args: Any, **kwargs: Any) -> Any:
            reason = None
            if getattr(self._settings, switch, False) is not True:
                reason = "switch off"
            elif self._allow is not None:
                reason = self._allow(attr, args, kwargs)
            if reason is None:
                return await target(*args, **kwargs)
            refused = Refused(self._name, attr, switch, reason, self._summarise(attr, args, kwargs))
            self.refused.append(refused)
            logger.warning("would have called %s.%s (%s: %s) %s",
                           self._name, attr, switch, reason, refused.summary)
            if self._on_refused is not None:
                result = self._on_refused(refused)
                if inspect.isawaitable(result):
                    await result
            return None

        return guarded_call
