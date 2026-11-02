"""Connections (design §3.1, §5.1, §5.3, FR-OB-74): configuration, the secret's
presence (never its value), the connection test and the login latch."""
from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from app.domain.config import AFTER_READ, CONNECTION_KINDS, CONNECTION_MODES
from app.domain.connections import check_connection
from app.integrations import shopify as shopify_mod
from app.integrations.sftp import readable
from app.models.config import Partner
from app.platform.errors import InvalidInput
from sqlalchemy import select
from app.models.config import Connection
from app.platform.authorization import requires
from app.platform.repository import Repository
from app.platform.services import CrudService
from app.platform.views import Preset
from app.schemas.connections import (
    ConnectionCreate, ConnectionRead, ConnectionTestRead, ConnectionUpdate, EdiSettings, FolderCheckRead, HostKeyRead, ShadowSetting,
)
from app.services.intake import ConnectionSnapshot
from app.services.problems import SFTP_AUTH_FAILED
from app.services.settings import ACK_TIMEOUT_MINUTES, EDI_CONNECTION_ID, SETTINGS, SHADOW_MODE

logger = logging.getLogger(__name__)

READ = "sales.read"
CONFIG = "sales.config"


_RULE_FIELDS = ("kind", "host", "username", "inbound_folder", "secret_name", "after_read", "processed_folder",
                "host_key", "api_version", "partner_code")
_FOLDERS = ("inbound_folder", "outbound_folder", "processed_folder", "error_folder",
            "test_inbound_folder", "test_outbound_folder")


def same_key(a: str | None, b: str | None) -> bool:
    """Host key lines compared on type and key (a trailing comment is ignored)."""
    return bool(a and b) and a.split()[:2] == b.split()[:2]


class ConnectionRepository(Repository[Connection]):
    model = Connection
    search_fields = ("name", "host")
    # secret_name and host_key are not list fields: never echoed back in a filter error.
    list_fields = ("name", "kind", "host", "port", "username", "after_read", "stable_seconds", "mode",
                   "auth_failed_at", "api_version", "cursor_updated_at", "page_size", "order_number_cutoff",
                   "partner_code", "active", "created_at", "updated_at")
    labels = {"after_read": "After read", "stable_seconds": "Stable (seconds)", "auth_failed_at": "Login refused at",
              "api_version": "API version", "cursor_updated_at": "Cursor (updated at)"}
    enums = {"kind": CONNECTION_KINDS, "after_read": AFTER_READ, "mode": CONNECTION_MODES}
    default_sort = ("name",)
    groupable = ("kind", "mode", "after_read", "partner_code", "active")
    presets = (Preset("all", "All"),
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
                   "active")
    unique_constraints = {"sales_connections_name_key": ("connection.name_taken", "name")}
    # Where secrets live (§5.3): the server environment, read for presence only.
    environ: ClassVar[Any] = os.environ

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

    async def before_update(self, entity: Connection, changes: dict[str, Any]) -> None:
        check_connection({**{k: getattr(entity, k) for k in _RULE_FIELDS}, **changes})
        await self._partner_exists(changes.get("partner_code"))

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
        return self.to_read(entity)

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
        folders = [getattr(entity, f) for f in _FOLDERS if getattr(entity, f)]
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
        return self.to_read(entity)


class EdiSettingsService:
    """The two P4 settings, and only those: the scheduler's settings are not
    editable here (it stays off until the release says otherwise). Changes
    are audited (sales_settings is Audited)."""

    def __init__(self, uow) -> None:
        self.uow = uow

    @requires(READ)
    async def get(self) -> EdiSettings:
        s = self.uow.session
        return EdiSettings(connection_id=await SETTINGS.get(s, EDI_CONNECTION_ID),
                           ack_timeout_minutes=await SETTINGS.get(s, ACK_TIMEOUT_MINUTES))

    @requires(CONFIG)
    async def update(self, body: EdiSettings) -> EdiSettings:
        s = self.uow.session
        if body.connection_id is not None:
            conn = await s.get(Connection, body.connection_id)
            if conn is None or conn.kind != "SFTP" or not conn.active:
                raise InvalidInput("edi.connection", "choose an active SFTP connection",
                                   {"connection_id": body.connection_id})
        self.uow.set_reason("EDI settings")
        await SETTINGS.set(s, EDI_CONNECTION_ID, body.connection_id)
        await SETTINGS.set(s, ACK_TIMEOUT_MINUTES, body.ack_timeout_minutes)
        return body


class ShadowSettingService:
    """The shadow-run switch (§5.4): sales.config to change; audited."""

    def __init__(self, uow) -> None:
        self.uow = uow

    @requires(READ)
    async def get(self) -> ShadowSetting:
        return ShadowSetting(shadow_mode=await SETTINGS.get(self.uow.session, SHADOW_MODE))

    @requires(CONFIG)
    async def update(self, body: ShadowSetting) -> ShadowSetting:
        """On: the switch and the open orders' flag in one unit (mark_open_orders).
        Off: the switch only — an order flagged stays Zoho's."""
        from app.services.shadow import mark_open_orders  # shadow → outbound → … : imported when used
        self.uow.set_reason("shadow run on" if body.shadow_mode else "shadow run off")
        await SETTINGS.set(self.uow.session, SHADOW_MODE, body.shadow_mode)
        marked = await mark_open_orders(self.uow) if body.shadow_mode else 0
        return ShadowSetting(shadow_mode=body.shadow_mode, marked_orders=marked)
