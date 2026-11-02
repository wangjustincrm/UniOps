"""The module's typed settings (design §3.1 `sales_settings`, §13.13). Values
live in sales_settings; a missing row means the default, a bad one the safe
value (safe-off). Edited on the settings page (P1 remainder)."""
from __future__ import annotations

import uuid
from typing import Any

from app.models.config import Setting as SettingRow
from app.platform.settings_registry import Setting, SettingsRegistry, as_bool, minimum


def _uuid_or_none(v: Any) -> uuid.UUID | None:
    return None if v in (None, "") else uuid.UUID(str(v))


SETTINGS = SettingsRegistry(SettingRow)

# FR-OB-07: every 5 minutes by default; can be paused. A bad stored interval
# reads as paused (safe-off), never as "run constantly".
SCHEDULER_INTERVAL_MINUTES = SETTINGS.declare(Setting(
    "scheduler.interval_minutes", 5, int, validate=minimum(1), safe=None,
    description="Minutes between cycles"))
SCHEDULER_PAUSED = SETTINGS.declare(Setting(
    "scheduler.paused", False, as_bool, description="Pause the scheduled cycle"))
# X2: every Action Required task goes to the operator.
OPERATOR_USER_ID = SETTINGS.declare(Setting(
    "operator_user_id", None, _uuid_or_none, safe=None, description="User who receives Action Required tasks"))
# P4: the warehouse's SFTP connection — 940s are put in its outbound folder
# (TEST mode: test_outbound_folder), its 997s are read from it. Unset: no 940
# is generated (fail closed); every queued order says why.
EDI_CONNECTION_ID = SETTINGS.declare(Setting(
    "edi.warehouse_connection_id", None, lambda v: None if v in (None, "") else int(v), safe=None,
    description="Warehouse SFTP connection for 940s and 997s"))
# FR-OB-23: no 997 within this time → Timeout and a task.
ACK_TIMEOUT_MINUTES = SETTINGS.declare(Setting(
    "edi.ack_timeout_minutes", 120, int, validate=minimum(1), safe=120,
    description="Minutes to wait for a 997 before Timeout"))
