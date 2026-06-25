"""Pydantic schemas for AuditLog (read-only)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    """Audit log rows are immutable at the DB level — no Create / Update
    schemas are exposed (writes happen via the internal crud.audit helper)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    user_id: uuid.UUID
    user_name: str
    action_type: str
    entity_type: str
    entity_id: uuid.UUID
    old_value: dict | None
    new_value: dict | None
    ip_address: str
    user_agent: str | None
    notes: str | None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
