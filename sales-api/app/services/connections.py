"""Connections (design §3.1, §5.1, §5.3, FR-OB-74): configuration, the secret's
presence (never its value), the connection test and the login latch."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, ClassVar

from app.core.config import settings as app_settings
from app.domain.schedule import is_overdue

from app.domain.config import AFTER_READ, CONNECTION_KINDS, CONNECTION_MODES
from app.domain.connections import check_connection
from app.integrations import shopify as shopify_mod
from app.integrations.sftp import readable
from app.models.config import Partner
from app.platform.errors import BusinessError, InvalidInput
from sqlalchemy import case, or_, select
from app.models.config import Connection
from app.platform.authorization import requires
from app.platform.query import FilterRule, parse_bool
from app.platform.repository import Repository
from app.platform.services import CrudService
from app.platform.settings_group import SettingsGroup
from app.platform.views import Preset
from app.schemas.connections import (
    ConnectionCreate, ConnectionRead, ConnectionTestRead, ConnectionUpdate, EdiSettings, FolderCheckRead, HostKeyRead,
    SchedulerSettings, ShadowSetting,
)
from app.services.cycle import window_of
from app.services.intake import ConnectionSnapshot
from app.services.problems import SFTP_AUTH_FAILED
from app.services.settings import (
    ACK_TIMEOUT_MINUTES, EDI_CONNECTION_ID, INVOICE_CONNECTION_ID, INVOICE_INTERNAL_EMAILS, SCHEDULER_PAUSED, SETTINGS, SHADOW_MODE, SYSTEM_MINUTES,
)

logger = logging.getLogger(__name__)

READ = "sales.read"
CONFIG = "sales.config"
FILE_RETRY = "sales.file.retry"
DEFAULT_MINUTES = {"SFTP": 5, "SHOPIFY": 10}  # a new connection's schedule (user 2026-11-02)


_RULE_FIELDS = ("kind", "host", "username", "inbound_folder", "secret_name", "after_read", "processed_folder",
                "host_key", "api_version", "partner_code")
_FOLDERS = ("inbound_folder", "outbound_folder", "processed_folder", "error_folder",
            "test_inbound_folder", "test_outbound_folder")
# What the connection test lists: test_inbound_folder stays a stored column (and in the CSV), but nothing reads it.
_TESTED_FOLDERS = tuple(f for f in _FOLDERS if f != "test_inbound_folder")


def same_key(a: str | None, b: str | None) -> bool:
    """Host key lines compared on type and key (a trailing comment is ignored)."""
    return bool(a and b) and a.split()[:2] == b.split()[:2]


# "Paused or failing": its schedule paused, its last run failed or partial, or its login latched.
NEEDS_ATTENTION = case((or_(Connection.schedule_paused.is_(True), Connection.last_run_status.in_(("FAILED", "PARTIAL")),
                            Connection.auth_failed_at.is_not(None)), True), else_=False)


class ConnectionRepository(Repository[Connection]):
    model = Connection
    search_fields = ("name", "host")
    # secret_name and host_key are not list fields: never echoed back in a filter error,
    # and not offered as columns either; test_inbound_folder is read by nothing.
    meta_hidden = ("secret_name", "host_key", "test_inbound_folder")
    list_fields = ("name", "kind", "host", "port", "username", "after_read", "stable_seconds", "mode",
                   "auth_failed_at", "api_version", "cursor_updated_at", "page_size", "order_number_cutoff",
                   "partner_code", "schedule_minutes", "schedule_paused", "last_run_at", "last_run_status", "next_run_at",
                   "active", "created_at", "updated_at")
    filterable = {"needs_attention": FilterRule(NEEDS_ATTENTION, kind="bool", cast=parse_bool)}
    labels = {"schedule_minutes": "Every (minutes)", "schedule_paused": "Paused", "last_run_at": "Last run",
              "last_run_status": "Last run status", "next_run_at": "Next run", "after_read": "After read", "stable_seconds": "Stable (seconds)", "auth_failed_at": "Login refused at",
              "api_version": "API version", "cursor_updated_at": "Cursor (updated at)"}
    enums = {"kind": CONNECTION_KINDS, "after_read": AFTER_READ, "mode": CONNECTION_MODES}
    default_sort = ("name",)
    groupable = ("kind", "mode", "after_read", "partner_code", "schedule_paused", "last_run_status", "active")
    presets = (Preset("all", "All"),
               Preset("paused_or_failing", "Paused or failing",
                      {"filters": [{"field": "needs_attention", "op": "eq", "value": "true"}]}),
               Preset("inactive", "Inactive", {"filters": [{"field": "active", "op": "eq", "value": "false"}]}),
               Preset("login_refused", "Login refused", {"filters": [{"field": "auth_failed_at", "op": "not_null", "value": None}]}))


class ConnectionService(CrudService[Connection, ConnectionRead, ConnectionCreate, ConnectionUpdate]):
    repo_cls = ConnectionRepository
    read_schema = ConnectionRead
    create_schema = ConnectionCreate
    update_schema = ConnectionUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = "name"
    csv_columns = ("name", "kind", "host", "port", "username", *_FOLDERS, "after_read", "stable_seconds", "mode",
                   "secret_name", "host_key", "api_version", "page_size", "order_number_cutoff", "partner_code",
                   "schedule_minutes", "schedule_paused", "active_from", "active_to", "active_days", "active")
    unique_constraints = {"sales_connections_name_key": ("connection.name_taken", "name")}
    # Where secrets live (§5.3): the server environment, read for presence only.
    environ: ClassVar[Any] = os.environ
    # The environment's switches (SALES_SCHEDULER_ENABLED): whether next runs ever happen.
    settings: ClassVar[Any] = app_settings
    clock: ClassVar[Any] = staticmethod(lambda: datetime.now(timezone.utc))

    def scheduler_enabled(self) -> bool:
        return bool(getattr(self.settings, "SALES_SCHEDULER_ENABLED", False))

    async def enrich(self, rows: list[ConnectionRead], entities) -> None:
        """Each row says whether its next run is overdue: only when the scheduler
        runs and is not paused as a whole (Health, Scheduler)."""
        if self.scheduler_enabled() and not await SETTINGS.get(self.uow.session, SCHEDULER_PAUSED):
            now = self.clock()
            for c in rows:
                c.overdue = is_overdue(now, next_run_at=c.next_run_at, minutes=c.schedule_minutes, active=c.active,
                                       paused=c.schedule_paused, window=window_of(c))

    def to_read(self, entity: Connection) -> ConnectionRead:
        out = super().to_read(entity)
        out.secret_present = bool(entity.secret_name and self.environ.get(entity.secret_name))
        return out

    async def _partner_exists(self, code: str | None) -> None:
        if code and (await self.uow.session.execute(select(Partner.id).where(Partner.code == code))).scalar_one_or_none() is None:
            raise InvalidInput("partner.unknown", f"no partner {code!r}", {"field": "partner_code"})

    async def validate_create(self, data: ConnectionCreate) -> None:
        check_connection(data.model_dump())
        await self._partner_exists(data.partner_code)

    def build(self, values: dict[str, Any]) -> Connection:
        if values.get("schedule_minutes") is None:  # by kind (user 2026-11-02)
            values["schedule_minutes"] = DEFAULT_MINUTES.get(values.get("kind"), 5)
        return super().build(values)

    async def before_update(self, entity: Connection, changes: dict[str, Any]) -> None:
        check_connection({**{k: getattr(entity, k) for k in _RULE_FIELDS}, **changes})
        await self._partner_exists(changes.get("partner_code"))
        start = changes.get("active_from", entity.active_from)
        end = changes.get("active_to", entity.active_to)
        if (start is None) != (end is None):
            raise InvalidInput("connection.active_window", "give both the start and the end of the active hours, or neither",
                               {"field": "active_to" if start is not None else "active_from"})
        if changes.get("mode") == "LIVE" and entity.mode != "LIVE":
            await self._go_live_gate()

    async def _go_live_gate(self) -> None:
        """PRD section 11 step 3 / AC-OB-71 (design §5.4): a channel goes LIVE
        only when every gate event has an active rule with recipients and a
        test notification was sent through it — the health page shows which."""
        from app.services.notifications import gate  # notifications → schemas; kept local
        missing = [g for g in await gate(self.uow.session) if not g.ok]
        if missing:
            raise InvalidInput(
                "connection.go_live_gate",
                "the notification gate is not met: " + "; ".join(f"{g.label} ({g.problem})" for g in missing),
                {"field": "mode", "events": [g.event_type for g in missing]})

    @requires(FILE_RETRY)
    async def run_now(self, id: int, scheduler, background) -> int:
        """This connection's job now (sales.file.retry): ignores its schedule,
        never its single flight — one already running is refused."""
        entity = await self.repo.get(id)
        if not entity.active:
            raise InvalidInput("connection.inactive", "activate the connection before running it")
        job = scheduler.connection(id)
        run_id = await job.claim(trigger="MANUAL", force=True)
        if run_id is None:
            raise BusinessError("run.already_running", f"{entity.name} is running; wait for it to finish")
        background.spawn(job.execute(run_id), name=f"sales-connection-{id}-run-{run_id}")
        return run_id

    @requires(CONFIG)
    async def reset_cursor(self, id: int, cursor) -> ConnectionRead:
        """FR-OB-10: where the next pull starts (inclusive); null pulls nothing.
        The only way a person changes the cursor — audited."""
        entity = await self.repo.get(id, for_update=True)
        if entity.kind != "SHOPIFY":
            raise InvalidInput("connection.cursor_shopify_only", "only a SHOPIFY connection has a cursor",
                               {"field": "cursor_updated_at"})
        self.uow.set_reason("reset cursor")
        entity.cursor_updated_at = cursor
        await self.uow.session.flush()
        return await self._read_one(entity)

    @requires(CONFIG)
    async def test(self, id: int, sftp, shopify=None) -> ConnectionTestRead:
        """Reads only (FR-OB-74). SFTP: the host key is read without logging
        in; the folders are listed only when the server's key is the pinned
        one. Shopify: the store's shop info, through its own gateway."""
        entity = await self.repo.get(id)
        snap = ConnectionSnapshot.of(entity)
        if entity.kind == "SHOPIFY":  # never SSH against a Shopify host
            try:
                info = await shopify.shop(snap)
            except Exception as e:  # noqa: BLE001
                logger.warning("connection test %s: shop: %r", entity.name, e)
                return ConnectionTestRead(host_key=None, folders=[], error=shopify_mod.readable(e))
            return ConnectionTestRead(host_key=None, folders=[], shop={
                k: info.get(k) for k in ("name", "domain", "myshopify_domain", "currency", "iana_timezone")})
        try:
            key = await sftp.host_key(snap)
        except Exception as e:  # noqa: BLE001 — the test reports, it does not fail
            logger.warning("connection test %s: host key: %r", entity.name, e)
            return ConnectionTestRead(host_key=None, folders=[], error=readable(e))
        pinned = bool(entity.host_key)
        matches = same_key(entity.host_key, key.line)
        result = ConnectionTestRead(host_key=HostKeyRead(line=key.line, fingerprint=key.fingerprint,
                                                         pinned=pinned, matches=matches), folders=[])
        if not matches:
            result.error = ("the host key is not pinned yet: compare the fingerprint and pin it" if not pinned
                            else "the server presents a different host key than the pinned one")
            return result
        folders = [getattr(entity, f) for f in _TESTED_FOLDERS if getattr(entity, f)]
        try:
            checks = await sftp.check(snap, folders)
            result.folders = [FolderCheckRead(folder=c.folder, ok=c.ok, files=c.files, error=c.error) for c in checks]
        except Exception as e:  # noqa: BLE001
            logger.warning("connection test %s: folders: %r", entity.name, e)
            result.error = readable(e)
        return result

    @requires(CONFIG)
    async def clear_latch(self, id: int, tasks) -> ConnectionRead:
        """A user clears a refused-login latch (FR-OB-50); the next cycle tries
        again. The problem's task is completed."""
        entity = await self.repo.get(id, for_update=True)
        self.uow.set_reason("clear auth latch")
        entity.auth_failed_at = None
        entity.auth_failed_reason = None
        await self.uow.session.flush()
        await tasks.resolve_problem(self.uow.session, SFTP_AUTH_FAILED, entity.id, by=self.uow.actor.user_id)
        return await self._read_one(entity)


class EdiSettingsService(SettingsGroup):
    """The warehouse connection (940s / 943s and 997s), the 997 wait, the 810 connection and the
    internal invoice recipients. Audited (sales_settings is Audited)."""
    registry = SETTINGS
    read_schema = EdiSettings
    fields = {"connection_id": EDI_CONNECTION_ID, "ack_timeout_minutes": ACK_TIMEOUT_MINUTES,
              "invoice_connection_id": INVOICE_CONNECTION_ID, "internal_emails": INVOICE_INTERNAL_EMAILS}
    read_permission, write_permission = READ, CONFIG
    reason = "EDI settings"

    async def check(self, changes: dict[str, Any]) -> None:
        for field_ in ("connection_id", "invoice_connection_id"):
            cid = changes.get(field_)
            if cid is not None:
                conn = await self.uow.session.get(Connection, cid)
                if conn is None or conn.kind != "SFTP" or not conn.active:
                    raise InvalidInput("edi.connection", "choose an active SFTP connection", {field_: cid})


class ShadowSettingService(SettingsGroup):
    """The shadow-run switch (§5.4): sales.config to change; audited."""
    registry = SETTINGS
    read_schema = ShadowSetting
    fields = {"shadow_mode": SHADOW_MODE}
    read_permission, write_permission = READ, CONFIG

    async def extra(self, values: dict[str, Any]) -> dict[str, Any]:
        """While it is off: the open orders switching it on would mark."""
        if values["shadow_mode"]:
            return {}
        from app.services.shadow import count_open_orders  # shadow → outbound → … : imported when used
        return {"open_orders": await count_open_orders(self.uow.session)}

    def reason_for(self, changes: dict[str, Any]) -> str:
        return "shadow run on" if changes["shadow_mode"] else "shadow run off"

    async def after(self, changes: dict[str, Any]) -> dict[str, Any]:
        """On: the open orders are flagged as Zoho's in the same unit. Off: an order flagged stays Zoho's."""
        if not changes["shadow_mode"]:
            return {"marked_orders": 0}
        from app.services.shadow import mark_open_orders
        return {"marked_orders": await mark_open_orders(self.uow)}


class SchedulerSettingsService(SettingsGroup):
    """Automatic runs and the system job's interval (Connections → the Automatic runs card):
    sales.config to change; audited. Saving the minutes never turns automatic runs on again
    (SchedulerUpdate: only what is sent changes)."""
    registry = SETTINGS
    read_schema = SchedulerSettings
    fields = {"paused": SCHEDULER_PAUSED, "system_minutes": SYSTEM_MINUTES}
    read_permission, write_permission = READ, CONFIG

    async def extra(self, values: dict[str, Any]) -> dict[str, Any]:
        return {"enabled": bool(app_settings.SALES_SCHEDULER_ENABLED)}

    def reason_for(self, changes: dict[str, Any]) -> str:
        if "paused" not in changes:
            return "scheduler settings"
        return "automatic runs off" if changes["paused"] else "automatic runs on"
