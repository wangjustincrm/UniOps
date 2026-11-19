"""Manual order settings (user 2026-11-13 / 2026-11-19), in one place where people look for them — beside the
orders, not among the EDI settings: whether Purolator Express is offered, the weight above which LTL is suggested,
why a manual order is made (the reasons, one required to submit), and which of those reasons are goods given away
(Orders → Free goods). Read with sales.read; changed with sales.config; audited."""
from __future__ import annotations

from typing import Any

from app.platform.errors import InvalidInput
from app.platform.settings_group import SettingsGroup
from app.schemas.orders import ManualOrderSettings
from app.services.settings import MANUAL_EXPRESS_OFFERED, MANUAL_FREE_REASONS, MANUAL_LTL_FROM_LB, MANUAL_REASONS, SETTINGS

READ, CONFIG = "sales.read", "sales.config"


class ManualOrderSettingsService(SettingsGroup):
    registry = SETTINGS
    read_schema = ManualOrderSettings
    fields = {"express_offered": MANUAL_EXPRESS_OFFERED, "ltl_from_lb": MANUAL_LTL_FROM_LB, "reasons": MANUAL_REASONS,
              "free_reasons": MANUAL_FREE_REASONS}
    read_permission, write_permission = READ, CONFIG
    reason = "manual order settings"

    async def check(self, changes: dict[str, Any]) -> None:
        """Free goods are reasons: renaming or removing a reason that is free goods without changing Free goods too is
        refused (the view would silently drop those orders, review 2026-11-19)."""
        values = {**await self._values(), **changes}
        missing = [r for r in values["free_reasons"] if r not in values["reasons"]]
        if missing:
            raise InvalidInput("manual.free_reason_unknown", "free goods must be reasons: " + ", ".join(missing)
                               + " is not in the reasons. Change Free goods too.", {"field": "free_reasons", "missing": missing})
