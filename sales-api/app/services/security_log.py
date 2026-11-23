"""The security log (≈ ABP SecurityLog, user 2026-11-23). Logins, MFA and role changes are identity-api's; Sales
records what it decides itself:

- every refusal (PermissionDenied → 403): a `sales_log` row, step "security", with the person, the permission, the
  route and the request id, in a session of its own (the refused request's unit of work rolls back);
- at startup, the switches the server runs with (values) and which SALES_SECRET_* variables are set (names only —
  never a value), on stdout and as a `sales_log` row.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.platform.authorization import on_denied
from app.platform.request_log import current_route
from app.services import oplog

STEP = "security"
SWITCHES = ("SALES_PARTNER_WRITES_ENABLED", "SALES_EXTERNAL_EMAIL_ENABLED", "SALES_SCHEDULER_ENABLED")
logger = logging.getLogger("sales.security")


@on_denied
async def log_refusal(uow: Any, key: str) -> None:
    factory = getattr(uow, "_session_factory", None)
    if factory is None:
        return
    a, route = uow.actor, current_route()
    async with factory() as s:
        await oplog.write(s, "WARNING", STEP, f"refused: {a.name} ({a.role or 'no role'}) needs {key}" + (f" on {route}" if route else ""),
                          context={"user_id": str(a.user_id) if a.user_id else None, "user_name": a.name, "role": a.role,
                                   "permission": key, "route": route})
        await s.commit()


def switches(settings: Any, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """What the server runs with: the switches' values, and the names (only) of the secrets that are set."""
    env = os.environ if environ is None else environ
    return {**{k: bool(getattr(settings, k, False)) for k in SWITCHES},
            "secrets_set": sorted(k for k, v in env.items() if k.startswith("SALES_SECRET_") and v)}


async def log_startup(settings: Any, session_factory) -> None:
    line = switches(settings)
    logger.info(json.dumps({"event": "startup_switches", **line}, separators=(",", ":")))
    try:
        async with session_factory() as s:
            on = ", ".join(f"{k}={'on' if line[k] else 'off'}" for k in SWITCHES)
            await oplog.write(s, "INFO", STEP, f"server started: {on}; secrets set: {', '.join(line['secrets_set']) or 'none'}",
                              context=line)
            await s.commit()
    except Exception:  # noqa: BLE001 — a log row never stops the server starting
        logger.exception("could not write the startup switches to sales_log")
