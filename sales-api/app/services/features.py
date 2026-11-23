"""Sales' switches as features (user 2026-11-23, ≈ ABP Feature management; platform/features.py): partner writes,
external email and the scheduler. The server's environment is the ceiling; a person can switch one OFF (and back to
the server's value) from Health, with a reason — an audited setting. Read with sales.read; changed with sales.config.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.platform.authorization import requires
from app.platform.errors import InvalidInput
from app.platform.features import FeatureDefinition, FeatureManager
from app.platform.services import Service
from app.platform.settings_registry import Setting
from app.services.settings import SETTINGS

READ, CONFIG = "sales.read", "sales.config"
TICK_SECONDS = 30
log = logging.getLogger("sales.features")


def _optional_bool(v: Any) -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    raise ValueError("on, off or none")


DEFINITIONS = [
    FeatureDefinition("SALES_PARTNER_WRITES_ENABLED", "Partner writes",
                      "Files to the warehouse and partners, and writes to Shopify and the gateways"),
    FeatureDefinition("SALES_EXTERNAL_EMAIL_ENABLED", "External email", "Emails to partners and customers"),
    FeatureDefinition("SALES_SCHEDULER_ENABLED", "Automatic runs on this server", "The scheduler: intake, sends, jobs"),
]
STORED = {d.name: SETTINGS.declare(Setting(f"feature.{d.name}", None, _optional_bool, safe=False,
                                           description=f"{d.display_name}: off by a person (the server's value is the ceiling)"))
          for d in DEFINITIONS}
FEATURES = FeatureManager(DEFINITIONS)


async def refresh(session) -> None:
    for name, setting in STORED.items():
        FEATURES.note_stored(name, await SETTINGS.get(session, setting))


class FeatureService(Service):
    def __init__(self, uow, settings: Any = None) -> None:
        super().__init__(uow)
        self.settings = settings

    @requires(READ)
    async def get(self) -> list[dict[str, Any]]:
        await refresh(self.s)
        return FEATURES.state(self.settings)

    @requires(CONFIG)
    async def set(self, name: str, value: bool | None, reason: str) -> list[dict[str, Any]]:
        if name not in STORED:
            raise InvalidInput("feature.unknown", f"no switch named {name}")
        if value is True:
            value = None  # "on" means back to the server's value: a stored value can only turn a switch off
        d = FEATURES.definitions[name]
        self.uow.set_reason(f"{d.display_name}: {'off' if value is False else 'back to the server'}: {reason}")
        await SETTINGS.set(self.s, STORED[name], value)
        FEATURES.note_stored(name, value)
        log.warning("switch %s %s by %s: %s", name, "off" if value is False else "back to the server", self.uow.actor.name, reason)
        return FEATURES.state(self.settings)


async def watch(session_factory, stop: asyncio.Event) -> None:
    """The tick: every TICK_SECONDS this process reads the stored values (a change made elsewhere)."""
    while not stop.is_set():
        try:
            async with session_factory() as s:
                await refresh(s)
        except Exception:  # noqa: BLE001 — the values stay as they were; try again next tick
            log.exception("could not read the switches")
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
