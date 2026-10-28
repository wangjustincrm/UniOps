"""Typed runtime settings (design §13.13).

    INTERVAL = settings.declare(Setting("scheduler.interval_minutes", 5, int, minimum(1)))
    minutes = await settings.get(session, INTERVAL)

Values live in the business code's settings table (`SettingRecordMixin`
columns; make the model Audited so every change — feature flags included —
lands in the audit trail). Reads go through one short-lived cache, so a change
is seen on the next tick without a restart.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from sqlalchemy import String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.errors import InvalidInput

T = TypeVar("T")


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


class SettingsRegistry:
    def __init__(self, model: type, *, ttl_seconds: float = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.model = model
        self.ttl = ttl_seconds
        self.clock = clock
        self._declared: dict[str, Setting] = {}
        self._cache: dict[str, Any] = {}
        self._loaded_at: float | None = None

    def declare(self, setting: Setting[T]) -> Setting[T]:
        if setting.key in self._declared:
            raise ValueError(f"setting {setting.key!r} declared twice")
        setting.coerce(setting.default)  # a default that fails its own validator is a bug
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
        try:
            return setting.coerce(self._cache[setting.key])
        except InvalidInput:
            # A bad stored value must not stop the scheduler; the default holds
            # until someone fixes it on the settings page.
            return setting.default

    async def set(self, session, setting: Setting[T], raw: Any) -> T:
        if setting.key not in self._declared:
            raise KeyError(f"setting {setting.key!r} is not declared")
        value = setting.coerce(raw)
        row = (await session.execute(
            select(self.model).where(self.model.key == setting.key).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            session.add(self.model(key=setting.key, value=value))
        else:
            row.value = value
        await session.flush()
        self.invalidate()
        return value
