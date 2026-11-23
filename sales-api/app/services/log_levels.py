"""Health → Log levels (user 2026-11-23): raise a logger for a while to see more while chasing an error — e.g. DEBUG on
the Shopify intake for 60 minutes — and it goes back by itself. Stored as the audited setting `logging.overrides`
(who, when, why), applied in this process on every change and by a tick (`watch`), so an expiry needs nobody.
Read with sales.read (Health shows it); changed with sales.config, with a reason. Output goes to the container's
stdout only (platform/log_levels.py), never to sales_log.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.platform import log_levels as levels
from app.platform.authorization import requires
from app.platform.errors import InvalidInput
from app.platform.services import Service
from app.services.settings import LOG_OVERRIDES, SETTINGS

READ, CONFIG = "sales.read", "sales.config"
TICK_SECONDS = 30
# The loggers offered in the form (any dotted name may be typed): "" is everything that is not quiet.
SUGGESTED = {
    "": "Everything (the root; the quiet third-party loggers stay quiet)",
    "sales.request": "The request log",
    "sales.security": "The security log",
    "app.services": "All of Sales' own work",
    "app.services.shopify_intake": "Shopify intake",
    "app.services.edi_intake": "EDI intake (850s)",
    "app.services.intake": "SFTP intake (files)",
    "app.services.outbound": "Outbound files (940, 810…)",
    "app.services.gateway_intake": "Authorize.Net intake",
    "app.services.paypal_intake": "PayPal intake",
    "app.integrations": "The clients: SFTP, Shopify, gateways, email",
    "app.platform.jobs": "The scheduler's jobs",
    "sqlalchemy.engine": "SQL statements (very loud)",
    "httpx": "HTTP calls (third party)",
    "asyncssh": "SSH / SFTP (third party)",
}
log = logging.getLogger("sales.log_levels")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def view(overrides: list[dict[str, Any]], default: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    return {"default_level": default, "overrides": levels.running(overrides, now), "suggested": SUGGESTED,
            "effective": levels.effective(list(SUGGESTED)), "max_minutes": levels.MAX_MINUTES,
            "default_minutes": levels.DEFAULT_MINUTES, "levels": list(levels.LEVELS)}


class LogLevelService(Service):
    settings: Any = None  # the app settings (LOG_LEVEL); the router passes them

    def __init__(self, uow, settings: Any = None) -> None:
        super().__init__(uow)
        self.settings = settings

    @property
    def _default(self) -> str:
        return str(getattr(self.settings, "LOG_LEVEL", "INFO") or "INFO").upper()

    @requires(READ)
    async def get(self) -> dict[str, Any]:
        return view(await SETTINGS.get(self.s, LOG_OVERRIDES), self._default)

    async def _write(self, overrides: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        now = _now()
        live = levels.running(overrides, now)  # expired ones are dropped as the list is written again
        self.uow.set_reason(reason)
        await SETTINGS.set(self.s, LOG_OVERRIDES, live)
        levels.apply(live, now)
        log.warning("log levels changed by %s: %s", self.uow.actor.name, reason)
        return view(live, self._default, now)

    @requires(CONFIG)
    async def raise_level(self, logger: str, level: str, minutes: int | None, reason: str) -> dict[str, Any]:
        minutes = levels.DEFAULT_MINUTES if minutes is None else minutes
        why = levels.check(logger, level, minutes)
        if why:
            raise InvalidInput("logging.invalid", why)
        current = [o for o in await SETTINGS.get(self.s, LOG_OVERRIDES) if o["logger"] != logger]
        o = levels.override(logger, level, minutes, _now(), self.uow.actor.name, reason)
        return await self._write([*current, o], f"log level {logger or 'root'} → {level} for {minutes} min: {reason}")

    @requires(CONFIG)
    async def clear(self, logger: str, reason: str) -> dict[str, Any]:
        current = await SETTINGS.get(self.s, LOG_OVERRIDES)
        if not any(o["logger"] == logger for o in current):
            raise InvalidInput("logging.not_raised", f"{logger or 'root'} is not raised")
        return await self._write([o for o in current if o["logger"] != logger], f"log level {logger or 'root'} back: {reason}")


async def watch(session_factory, stop: asyncio.Event) -> None:
    """The tick: every TICK_SECONDS this process applies the stored overrides (a change made elsewhere, an expiry)."""
    while not stop.is_set():
        try:
            async with session_factory() as s:
                levels.apply(await SETTINGS.get(s, LOG_OVERRIDES), _now())
        except Exception:  # noqa: BLE001 — the levels stay as they are; try again next tick
            log.exception("could not apply the log levels")
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
