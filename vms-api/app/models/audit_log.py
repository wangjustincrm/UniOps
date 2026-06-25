"""AuditLog ORM model — immutable activity record.

Per VMS PRD §5.2 / §2.5.2 VMS-AU-001…003. BIGINT autoincrement PK (not UUID)
matches the high-write, sequential-scan workload of an audit table.
Immutability is enforced at the DB level via a separate migration that runs
`REVOKE UPDATE, DELETE ON vms_audit_logs FROM epms`.
"""
import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "vms_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    user_id:     Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_name:   Mapped[str] = mapped_column(String(200), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id:   Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    old_value:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address:  Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent:  Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes:       Mapped[str | None] = mapped_column(Text, nullable=True)
