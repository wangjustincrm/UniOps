"""Sales' audit logs (sales36, ≈ ABP's AbpAuditLogs / AbpAuditLogActions; platform/auditing): the SQL store and the read
side of Operations → Audit log (sales.config for now — the user, 2026-11-23: no new identity key yet).

SqlAuditingStore (the Strategy the composition root sets) writes a log and its actions in a session of its own
(≈ ABP's UnitOfWorkManager.Begin(true)), so the log of a failed request is kept although the request rolled back.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.records import AuditLog, AuditLogAction, AuditRecord
from app.platform.authorization import requires
from app.platform.errors import NotFound
from app.platform.query import FilterRule
from app.platform.resource import Resource
from app.platform.views import Preset
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.schemas.audit_logs import AuditLogActionRead, AuditLogDetail, AuditLogRead, EntityChangeRead
from app.platform.auditing.scope import AuditLogInfo


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


class SqlAuditingStore:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def save(self, log: AuditLogInfo) -> None:
        async with self.session_factory() as s:
            s.add(AuditLog(
                id=log.id, kind=log.kind, name=log.name, application_name=log.application_name or None,
                correlation_id=log.correlation_id, user_id=_uuid(log.user_id), user_name=log.user_name, role=log.role,
                client_ip=log.client_ip, browser_info=log.browser_info, http_method=log.http_method, url=log.url,
                route=log.route, http_status=log.http_status, execution_time=log.execution_time,
                execution_duration_ms=log.execution_duration_ms, error_code=log.error_code,
                exceptions="\n\n".join(log.exceptions) or None, comments=log.comments or None,
                entity_changes=log.entity_changes, extra=log.extra or None))
            await s.flush()
            for a in log.actions:
                s.add(AuditLogAction(audit_log_id=log.id, service_name=a.service_name, method_name=a.method_name,
                                     parameters=a.parameters, execution_time=a.execution_time,
                                     execution_duration_ms=a.execution_duration_ms))
            await s.commit()


async def actions_of(session, audit_log_id: uuid.UUID) -> list[AuditLogAction]:
    return list((await session.execute(select(AuditLogAction).where(AuditLogAction.audit_log_id == audit_log_id)
                                       .order_by(AuditLogAction.id))).scalars())


AUDIT = "sales.config"  # the Audit Log's permission until the user decides on one of its own

class AuditLogRepository(Repository[AuditLog]):
    model = AuditLog
    search_fields = ("correlation_id", "user_name", "route", "url", "error_code", "name")
    list_fields = ("id", "kind", "name", "correlation_id", "user_name", "role", "client_ip", "http_method", "route",
                   "http_status", "execution_time", "execution_duration_ms", "error_code", "entity_changes")
    labels = {"execution_time": "Time", "execution_duration_ms": "Duration (ms)", "http_method": "Method",
              "http_status": "Status", "user_name": "User", "correlation_id": "Correlation id", "error_code": "Error",
              "entity_changes": "Changes", "kind": "Kind", "route": "Route", "client_ip": "IP"}
    enums = {"kind": ("request", "job", "outbox", "intake", "cli"), "http_method": ("GET", "POST", "PUT", "PATCH", "DELETE")}
    filterable = {"has_exception": FilterRule(AuditLog.exceptions.is_not(None), kind="bool", cast=lambda v: str(v).lower() == "true")}
    default_sort = ("-execution_time",)
    meta_hidden = ("id", "user_id")  # UUIDs: the rows carry them (the page opens a log by its id), never a column
    groupable = ("kind", "user_name", "route", "http_status", "http_method", "error_code")
    suggestable = ("user_name", "route", "error_code")
    presets = (
        Preset("all", "All"),
        Preset("failed", "Failed", {"filters": [{"field": "http_status", "op": "gte", "value": 400}]}),
        Preset("changed", "Changed records", {"filters": [{"field": "entity_changes", "op": "gt", "value": 0}]}),
        Preset("background", "Runs and sends", {"filters": [{"field": "kind", "op": "in", "value": ["job", "outbox", "intake"]}]}),
    )


class AuditLogService(ReadOnlyService[AuditLog, AuditLogRead]):
    repo_cls = AuditLogRepository
    read_schema = AuditLogRead
    read_permission = AUDIT
    resource = Resource(path="/audit-logs", tags=("audit",), export=False)  # who did what, with parameters: read here, never handed out in bulk

    def to_read(self, e: AuditLog) -> AuditLogRead:
        out = AuditLogRead.model_validate(e, from_attributes=True)
        out.has_exception = e.exceptions is not None
        return out

    @requires(AUDIT)
    async def detail(self, id: uuid.UUID) -> AuditLogDetail:
        s = self.uow.session
        log = await s.get(AuditLog, id)
        if log is None:
            raise NotFound("AuditLog", id)
        changes = (await s.execute(select(AuditRecord).where(AuditRecord.audit_log_id == id).order_by(AuditRecord.id))).scalars()
        return AuditLogDetail(**self.to_read(log).model_dump(), exceptions=log.exceptions, comments=log.comments, extra=log.extra,
                              actions=[AuditLogActionRead.model_validate(a) for a in await actions_of(s, id)],
                              changes=[EntityChangeRead.model_validate(c) for c in changes])


class EntityChangeRepository(Repository[AuditRecord]):
    """≈ ABP Pro's Entity changes tab: every change, by entity type and id."""
    model = AuditRecord
    search_fields = ("entity", "entity_id", "user_name", "reason")
    list_fields = ("id", "at", "user_name", "action", "entity", "entity_id", "reason", "audit_log_id")
    labels = {"at": "Time", "user_name": "User", "entity": "Entity", "entity_id": "Id", "audit_log_id": "Audit log"}
    enums = {"action": ("create", "update", "delete", "send_again", "fulfil_in_shopify")}
    default_sort = ("-at",)
    meta_hidden = ("audit_log_id",)  # a UUID: the row carries it (Open its request), never a column
    groupable = ("entity", "action", "user_name")
    suggestable = ("entity", "user_name")
    presets = (Preset("all", "All"), Preset("orders", "Orders", {"filters": [{"field": "entity", "op": "eq", "value": "sales_orders"}]}))


class EntityChangeService(ReadOnlyService[AuditRecord, EntityChangeRead]):
    repo_cls = EntityChangeRepository
    read_schema = EntityChangeRead
    read_permission = AUDIT
    resource = Resource(path="/entity-changes", tags=("audit",), export=False)  # before / after values of every record: read here, never handed out in bulk
