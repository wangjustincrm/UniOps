"""Connections (design §3.1, §5.1, §5.3, FR-OB-74): configuration, the secret's
presence (never its value), the connection test and the login latch."""
from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from app.domain.connections import check_connection
from app.integrations.sftp import readable
from app.models.config import Connection
from app.platform.authorization import requires
from app.platform.repository import FilterRule, Repository, parse_bool
from app.platform.services import CrudService
from app.schemas.connections import (
    ConnectionCreate, ConnectionRead, ConnectionTestRead, ConnectionUpdate, FolderCheckRead, HostKeyRead,
)
from app.services.intake import ConnectionSnapshot
from app.services.problems import SFTP_AUTH_FAILED

logger = logging.getLogger(__name__)

READ = "sales.read"
CONFIG = "sales.config"

SHOPIFY_TEST_LATER = "Shopify connection test arrives with the Shopify gateway (P3)"

_RULE_FIELDS = ("kind", "host", "username", "inbound_folder", "secret_name", "after_read", "processed_folder",
                "host_key")
_FOLDERS = ("inbound_folder", "outbound_folder", "processed_folder", "error_folder",
            "test_inbound_folder", "test_outbound_folder")


def same_key(a: str | None, b: str | None) -> bool:
    """Host key lines compared on type and key (a trailing comment is ignored)."""
    return bool(a and b) and a.split()[:2] == b.split()[:2]


class ConnectionRepository(Repository[Connection]):
    model = Connection
    search_fields = ("name", "host")
    sortable = {"name": Connection.name, "kind": Connection.kind, "mode": Connection.mode, "active": Connection.active}
    filterable = {
        "kind": FilterRule(Connection.kind), "mode": FilterRule(Connection.mode),
        "active": FilterRule(Connection.active, ops=frozenset({"eq"}), cast=parse_bool),
    }
    default_sort = ("name",)


class ConnectionService(CrudService[Connection, ConnectionRead, ConnectionCreate, ConnectionUpdate]):
    repo_cls = ConnectionRepository
    read_schema = ConnectionRead
    create_schema = ConnectionCreate
    update_schema = ConnectionUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = "name"
    csv_columns = ("name", "kind", "host", "port", "username", *_FOLDERS, "after_read", "stable_seconds", "mode",
                   "secret_name", "host_key", "active")
    unique_constraints = {"sales_connections_name_key": ("connection.name_taken", "name")}
    # Where secrets live (§5.3): the server environment, read for presence only.
    environ: ClassVar[Any] = os.environ

    def to_read(self, entity: Connection) -> ConnectionRead:
        out = super().to_read(entity)
        out.secret_present = bool(entity.secret_name and self.environ.get(entity.secret_name))
        return out

    async def validate_create(self, data: ConnectionCreate) -> None:
        check_connection(data.model_dump())

    async def before_update(self, entity: Connection, changes: dict[str, Any]) -> None:
        check_connection({**{k: getattr(entity, k) for k in _RULE_FIELDS}, **changes})

    @requires(CONFIG)
    async def test(self, id: int, sftp) -> ConnectionTestRead:
        """Reads only (FR-OB-74). The host key is read without logging in; the
        folders are listed only when the server's key is the pinned one."""
        entity = await self.repo.get(id)
        if entity.kind != "SFTP":  # never SSH against a Shopify host (P3 tests it through its own gateway)
            return ConnectionTestRead(host_key=None, folders=[], error=SHOPIFY_TEST_LATER)
        snap = ConnectionSnapshot.of(entity)
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
