"""Manual order settings (user 2026-11-13 / 2026-11-19), in one place where people look for them — beside the
orders, not among the EDI settings: whether Purolator Express is offered, the weight above which LTL is suggested,
why a manual order is made (the reasons, one required to submit; Orders → Manual orders by reason). Read with sales.read; changed with sales.config; audited."""
from __future__ import annotations

from app.platform.settings_group import SettingsGroup
from app.schemas.orders import ManualOrderSettings
from app.services.settings import MANUAL_EXPRESS_OFFERED, MANUAL_LTL_FROM_LB, MANUAL_REASONS, SETTINGS

READ, CONFIG = "sales.read", "sales.config"


class ManualOrderSettingsService(SettingsGroup):
    registry = SETTINGS
    read_schema = ManualOrderSettings
    fields = {"express_offered": MANUAL_EXPRESS_OFFERED, "ltl_from_lb": MANUAL_LTL_FROM_LB, "reasons": MANUAL_REASONS}
    read_permission, write_permission = READ, CONFIG
    reason = "manual order settings"
    path, tags = "/config/manual-order-settings", ("orders",)
