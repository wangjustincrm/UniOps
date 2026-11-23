"""The audit log's tables as mixins (as platform.audit.AuditRecordMixin): the module chooses the names (its prefix),
so the platform knows no module. ≈ ABP's AbpAuditLogs / AbpAuditLogActions; the entity changes are the module's
audit-record table (with audit_log_id) and the property changes a view over it."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class AuditLogMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    browser_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    exceptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[Any] = mapped_column(JSONB, nullable=True)
    entity_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra: Mapped[Any] = mapped_column(JSONB, nullable=True)


class AuditLogActionMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # the module adds: audit_log_id = mapped_column(ForeignKey("<prefix>_audit_logs.id"), nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    method_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parameters: Mapped[Any] = mapped_column(JSONB, nullable=True)
    execution_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
