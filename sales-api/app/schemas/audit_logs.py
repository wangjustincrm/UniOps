"""Operations → Audit log (sales36, ≈ ABP Pro's audit log UI): a log, its actions and its entity changes."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str | None = None
    correlation_id: str
    user_id: uuid.UUID | None = None
    user_name: str | None = None
    role: str | None = None
    client_ip: str | None = None
    browser_info: str | None = None
    http_method: str | None = None
    url: str | None = None
    route: str | None = None
    http_status: int | None = None
    execution_time: datetime
    execution_duration_ms: int | None = None
    error_code: str | None = None
    has_exception: bool = False
    entity_changes: int = 0


class AuditLogActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_name: str
    method_name: str
    parameters: Any = None
    execution_time: datetime
    execution_duration_ms: int | None = None


class EntityChangeRead(BaseModel):
    """≈ ABP's EntityChange: one row of sales_audit (its before / after whole)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    user_name: str
    action: str
    entity: str
    entity_id: str | None = None
    before: Any = None
    after: Any = None
    reason: str | None = None
    audit_log_id: uuid.UUID | None = None


class AuditLogDetail(AuditLogRead):
    exceptions: str | None = None
    comments: Any = None
    extra: Any = None
    actions: list[AuditLogActionRead] = []
    changes: list[EntityChangeRead] = []
