"""Sales' audit logs (sales36, ≈ ABP's AbpAuditLogs / AbpAuditLogActions; platform/auditing): the SQL store and the read
side of Operations → Audit log.

SqlAuditingStore (the Strategy the composition root sets) writes a log and its actions in a session of its own
(≈ ABP's UnitOfWorkManager.Begin(true)), so the log of a failed request is kept although the request rolled back.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.records import AuditLog, AuditLogAction
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
