"""≈ ABP's [Audited] / [DisableAuditing] (the Decorator pattern): on an endpoint they force its request's audit log
saved, or never saved, whatever the options say. On a model, `__disable_auditing__ = True` is the same switch for the
audit trail — a mechanism only: nothing uses it (the user's rule: remove nothing)."""
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


def audited(fn: F) -> F:
    fn.__auditing__ = True  # type: ignore[attr-defined]
    return fn


def disable_auditing(fn: F) -> F:
    fn.__auditing__ = False  # type: ignore[attr-defined]
    return fn
