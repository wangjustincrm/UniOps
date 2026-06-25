"""Universal audit log — FIN-AUD-001/005 (Phase 0-B4). identity-api OWNS this table.

Append-only: rows are never updated or deleted (CRA retention ≥ 6 years).
Phase 0 writers: identity auth events (login/login_failed/password_changed/
mfa_enabled/mfa_disabled). Other services adopt the same helper pattern later.
"""
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AuditLog(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service: Mapped[str] = mapped_column(String(20), nullable=False)        # identity | epms | ...
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    object_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
