"""Read-only mirror of epms-api's `company_config` table.

vms-api needs to read SMTP credentials to send Training / PPE notifications
(PRD §2.1.2.1). epms-api owns the schema; this mirror only exposes the
fields vms-api actually reads, so adding columns upstream doesn't break us.

Registered with `Base.metadata` so test infra (Base.metadata.create_all)
materializes the table. Production alembic migrations DO NOT touch it —
the table is created by epms-api.
"""
import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyConfig(Base):
    """Read-only mirror — fields only as needed by vms-api."""
    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    smtp_host:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls:  Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_from:     Mapped[str | None] = mapped_column(String(255), nullable=True)
