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


def email_list(v: Any) -> list[str]:
    """A list, or one string split on commas / semicolons; blanks dropped, order kept, no repeats."""
    items = v if isinstance(v, (list, tuple)) else str(v or "").replace(";", ",").split(",")
    out: list[str] = []
    for x in (str(i).strip() for i in items):
        if x and x.lower() not in (o.lower() for o in out):
            out.append(x)
    return out


def _addresses(v: list[str]) -> str | None:
    from app.integrations.email import plain_address
    bad = [x for x in v if plain_address(x) != x]  # one plain mailbox each, as the internal-mail check reads it
    return f"not an email address: {', '.join(bad)}" if bad else (None if len(v) <= 20 else "at most 20 addresses")


SETTINGS = SettingsRegistry(SettingRow)

# FR-OB-07: every 5 minutes by default; can be paused. A bad stored interval
# reads as paused (safe-off), never as "run constantly". Superseded by each
# connection's own schedule and `scheduler.system_minutes` (2026-11-02): no
# job reads the interval any more; `scheduler.paused` stays the master switch.
SCHEDULER_INTERVAL_MINUTES = SETTINGS.declare(Setting(
    "scheduler.interval_minutes", 5, int, validate=minimum(1), safe=None,
    description="Minutes between cycles"))
# The user's switch, shown as Automatic runs On / Off (2026-11-04). Off until
# someone turns it on: a database that never set it (a fresh one, or a copy of
# another server's) starts nothing, even with SALES_SCHEDULER_ENABLED on. A bad
# stored value reads as Off too.
SCHEDULER_PAUSED = SETTINGS.declare(Setting(
    "scheduler.paused", True, as_bool, safe=True, description="Automatic runs off: no job runs on its schedule"))
# X2: every Action Required task goes to the operator.
OPERATOR_USER_ID = SETTINGS.declare(Setting(
    "operator_user_id", None, _uuid_or_none, safe=None, description="User who receives Action Required tasks"))
# P4: the warehouse's SFTP connection — 940s are put in its outbound folder
# (TEST mode: test_outbound_folder), its 997s are read from it. Unset: no 940
# is generated (fail closed); every queued order says why.
EDI_CONNECTION_ID = SETTINGS.declare(Setting(
    "edi.warehouse_connection_id", None, lambda v: None if v in (None, "") else int(v), safe=None,
    description="Warehouse SFTP connection for 940s and 997s"))
# Shadow run (design §5.4; user decision 2026-11-02): Zoho stays the system
# of record. While on: after-read is Leave whatever the connection says,
# inbound documents are flagged `shadow`, orders received are flagged
# `shadow` (never sent a 940 automatically, even after it is switched off),
# no 940 is generated or put, and only intake / connection problems raise
# tasks. A bad stored value reads as ON — the restrictive side.
SHADOW_MODE = SETTINGS.declare(Setting(
    "shadow_mode", False, as_bool, safe=True, description="Shadow run: read only, send nothing (Zoho is live)"))
# P6: the SFTP connection 810 JSON invoices are put to (TrueCommerce Import);
# TEST mode: its test outbound folder. Unset: an 810 is not sent (it fails with a task).
INVOICE_CONNECTION_ID = SETTINGS.declare(Setting(
    "invoice.connection_id", None, lambda v: None if v in (None, "") else int(v), safe=None,
    description="SFTP connection for 810 invoices"))
# FR-OB-32, widened by the user 2026-11-09: every time an invoice is sent, these company addresses get a
# message of their own with all its documents (invoice, PO, packing list, and the 810 file when it is
# one), whatever the partner's channel does and also in TEST. Sent as internal mail
# (SALES_INTERNAL_EMAIL_ENABLED + SALES_INTERNAL_EMAIL_DOMAINS). Empty: nobody.
INVOICE_INTERNAL_EMAILS = SETTINGS.declare(Setting(
    "invoice.internal_emails", [], email_list, validate=_addresses, safe=[],
    description="Company addresses that get the documents of every invoice sent"))
# FR-OB-23: no 997 within this time → Timeout and a task.
ACK_TIMEOUT_MINUTES = SETTINGS.declare(Setting(
    "edi.ack_timeout_minutes", 120, int, validate=minimum(1), safe=120,
    description="Minutes to wait for a 997 before Timeout"))
# P8 (FR-OB-52a): the health page's thresholds.
TASK_AGE_HOURS = SETTINGS.declare(Setting(
    "health.task_age_hours", 24, int, validate=minimum(1), safe=24,
    description="Hours after which an open task shows on the health page"))
OUTBOX_BACKLOG_MINUTES = SETTINGS.declare(Setting(
    "health.outbox_backlog_minutes", 30, int, validate=minimum(1), safe=30,
    description="Minutes a queued send may wait before the health page warns"))
# Per-connection scheduling (user 2026-11-02): the system job (queue 940 / 943,
# the outbox, ACK timeouts, invoice advance, the shadow comparison) runs on its
# own interval; `scheduler.paused` stays the master switch for every job.
SYSTEM_MINUTES = SETTINGS.declare(Setting(
    "scheduler.system_minutes", 1, int, validate=minimum(1), safe=1,
    description="Minutes between runs of the system job"))
