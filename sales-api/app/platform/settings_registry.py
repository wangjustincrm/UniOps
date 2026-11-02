"""Typed runtime settings (design §13.13).

    INTERVAL = settings.declare(Setting("scheduler.interval_minutes", 5, int, minimum(1)))
    minutes = await settings.get(session, INTERVAL)

Values live in the business code's settings table (`SettingRecordMixin`
columns; make the model Audited so every change — feature flags included —
lands in the audit trail). Reads go through one short-lived cache, so a change
is seen on the next tick without a restart; a change made here empties the
cache when its transaction commits.

A stored value that no longer parses is SAFE-OFF (product owner, 2026-10-28):
the read returns the setting's `safe` value — never its default, which may be
"on" — and `on_invalid` is called (the business code raises a task there).
A boolean's safe value is False; every other setting must declare one (None
is allowed and means "off" to the caller, e.g. a paused schedule).
"""
from __future__ import annotations

import logging
import uuid
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import String, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.errors import InvalidInput

T = TypeVar("T")

logger = logging.getLogger(__name__)

_UNSET: Any = object()


class SettingRecordMixin:
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)


@dataclass(frozen=True)
class Setting(Generic[T]):
    key: str
    default: T
    parse: Callable[[Any], T]
    validate: Callable[[T], str | None] | None = None  # → error message or None
    description: str = ""
    # What a read returns when the stored value is bad. Booleans: False.
    safe: T | None = _UNSET

    @property
    def safe_value(self) -> T | None:
        if self.safe is not _UNSET:
            return self.safe
        if self.parse is as_bool or isinstance(self.default, bool):
            return False  # type: ignore[return-value]
        raise ValueError(f"setting {self.key!r} needs an explicit safe value")

    def coerce(self, raw: Any) -> T:
        try:
            value = self.parse(raw)
        except (TypeError, ValueError) as e:
            raise InvalidInput("setting.invalid", f"{self.key}: {e}", {"key": self.key}) from e
        if self.validate is not None:
            msg = self.validate(value)
            if msg:
                raise InvalidInput("setting.invalid", f"{self.key}: {msg}", {"key": self.key})
        return value


def minimum(n: float) -> Callable[[Any], str | None]:
    return lambda v: None if v >= n else f"must be at least {n}"


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {v!r}")


OnInvalid = Callable[[Any, Setting, Any, InvalidInput], Awaitable[None]]  # (session, setting, raw, error)


class SettingsRegistry:
    def __init__(
        self,
        model: type,
        *,
        ttl_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        on_invalid: OnInvalid | None = None,
    ) -> None:
        self.model = model
        self.on_invalid = on_invalid
        self.ttl = ttl_seconds
        self.clock = clock
        self._declared: dict[str, Setting] = {}
        self._cache: dict[str, Any] = {}
        self._loaded_at: float | None = None

    def declare(self, setting: Setting[T]) -> Setting[T]:
        if setting.key in self._declared:
            raise ValueError(f"setting {setting.key!r} declared twice")
        setting.coerce(setting.default)  # a default that fails its own validator is a bug
        if setting.safe_value is not None:
            setting.coerce(setting.safe_value)  # so is a safe value that does
        self._declared[setting.key] = setting
        return setting

    @property
    def declared(self) -> list[Setting]:
        return list(self._declared.values())

    def invalidate(self) -> None:
        self._loaded_at = None

    async def _load(self, session) -> None:
        if self._loaded_at is not None and self.clock() - self._loaded_at < self.ttl:
            return
        rows = (await session.execute(select(self.model.key, self.model.value))).all()
        self._cache = {k: v for k, v in rows}
        self._loaded_at = self.clock()

    async def get(self, session, setting: Setting[T]) -> T:
        if setting.key not in self._declared:
            raise KeyError(f"setting {setting.key!r} is not declared")
        await self._load(session)
        if setting.key not in self._cache or self._cache[setting.key] is None:
            return setting.default
        raw = self._cache[setting.key]
        try:
            return setting.coerce(raw)
        except InvalidInput as e:
            # Safe-off until someone fixes it on the settings page.
            logger.error("setting %s has a bad stored value %r (%s); using %r", setting.key, raw, e, setting.safe_value)
            if self.on_invalid is not None:
                try:
                    await self.on_invalid(session, setting, raw, e)
                except Exception:  # noqa: BLE001 — a failed report never fails the read
                    logger.exception("on_invalid for setting %s failed", setting.key)
            return setting.safe_value

    async def set(self, session, setting: Setting[T], raw: Any) -> T:
        if setting.key not in self._declared:
            raise KeyError(f"setting {setting.key!r} is not declared")
        value = setting.coerce(raw)
        stored = str(value) if isinstance(value, uuid.UUID) else value  # JSONB holds no UUID; parse reads it back
        row = (await session.execute(
            select(self.model).where(self.model.key == setting.key).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            session.add(self.model(key=setting.key, value=stored))
        else:
            row.value = stored
        await session.flush()
        # Readers see the new value once it is committed — not before (they
        # would cache the old one for a whole TTL after the commit).
        sync = getattr(session, "sync_session", session)
        event.listen(sync, "after_commit", lambda _s: self.invalidate(), once=True)
        return value
