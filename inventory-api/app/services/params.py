"""Inventory settings stored in `inv_params`, with their defaults and
validation in one place so the API, the scheduler and the query layer agree.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvParam

NC_SYNC_INTERVAL_KEY = "nc_sync_interval_minutes"
NC_TIME_ZONE_KEY = "nc_time_zone"
QTY_TOLERANCE_KEY = "qty_tolerance"
AMOUNT_TOLERANCE_KEY = "amount_tolerance"

# NC writes business times in the application server's clock, which is
# hard-coded to China time (design D14). The switch exists for the day NC is
# fixed, or for checking a suspicion — hence only these two choices.
NC_TIME_ZONES = ("Asia/Shanghai", "America/Toronto")
LOCAL_TIME_ZONE = "America/Toronto"

DEFAULTS: dict[str, Any] = {
    NC_SYNC_INTERVAL_KEY: 60,
    NC_TIME_ZONE_KEY: "Asia/Shanghai",
    QTY_TOLERANCE_KEY: 0.001,
    AMOUNT_TOLERANCE_KEY: 0.01,
}


class InvalidParam(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    """Return the normalised value or raise InvalidParam with a message fit
    for a 422."""
    if key not in DEFAULTS:
        raise InvalidParam(f"Unknown setting {key!r}")
    if key == NC_SYNC_INTERVAL_KEY:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1440:
            raise InvalidParam("Sync interval must be a whole number of minutes between 0 and 1440 (0 = off)")
        return value
    if key == NC_TIME_ZONE_KEY:
        if value not in NC_TIME_ZONES:
            raise InvalidParam(f"NC time zone must be one of {', '.join(NC_TIME_ZONES)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 1000:
        raise InvalidParam("Tolerance must be a number between 0 and 1000")
    return float(value)


def coerce(key: str, raw: Any) -> Any:
    """Whatever is stored (possibly written by hand), return something usable:
    an invalid stored value falls back to the default rather than breaking the
    scheduler or a query."""
    try:
        return validate(key, raw)
    except InvalidParam:
        return DEFAULTS[key]


async def get_param(db: AsyncSession, key: str) -> Any:
    row = await db.get(InvParam, key)
    return coerce(key, row.value) if row is not None else DEFAULTS[key]


async def get_all(db: AsyncSession) -> dict[str, Any]:
    return {k: await get_param(db, k) for k in DEFAULTS}


async def set_param(db: AsyncSession, key: str, value: Any, actor: uuid.UUID | None) -> Any:
    value = validate(key, value)
    row = await db.get(InvParam, key)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(InvParam(key=key, value=value, updated_by=actor, updated_at=now))
    else:
        row.value, row.updated_by, row.updated_at = value, actor, now
    return value


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover — tzdata missing in image
        raise RuntimeError(f"Time zone {name!r} is not available; install tzdata") from None
