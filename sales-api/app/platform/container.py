"""Composition-root helpers (design §13.14).

The business code's composition root (`app/container.py`) builds every
gateway once and hands the same objects to the API and the scheduler. In the
test environment it uses fakes; elsewhere the real gateways wrapped in
`GuardedGateway` with the switch names it chooses. No DI framework.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from app.platform.gateways.guard import GuardedGateway

G = TypeVar("G")

TEST_ENVIRONMENT = "test"


def is_test(settings: Any) -> bool:
    return getattr(settings, "ENVIRONMENT", None) == TEST_ENVIRONMENT


def gateway(
    settings: Any,
    *,
    real: Callable[[], G],
    fake: Callable[[], G],
    guarded: Mapping[str, str],
    **guard_kwargs: Any,
) -> Any:
    """A fake in tests; otherwise the real gateway behind its guard. `real`
    is a factory, so tests never construct a real client."""
    if is_test(settings):
        return fake()
    return GuardedGateway(real(), settings=settings, guarded=guarded, **guard_kwargs)
