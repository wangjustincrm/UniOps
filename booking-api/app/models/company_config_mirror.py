"""Read-only mirror of epms-api's `company_config` table.

booking-api does NOT own this table — epms-api is the writer of record and
owns its schema and migrations.  Registering the mirror with `Base.metadata`
here lets booking-api:

  1. Query `role_permissions` (JSONB) in `permissions._load_matrix()` to
     enforce the shared Access Control Matrix.
  2. Query SMTP columns in `_load_smtp_config()` as a fallback when
     booking_config.smtp_settings is not set.
  3. In tests, have `Base.metadata.create_all()` materialise the table so
     both consumers above can execute without a savepoint guard or bare except.

The booking-api Alembic migrations DO NOT create or alter this table; it must
already exist (in production: created by epms-api migrations).

Physical column types verified against epms DB 2026-07-08:
  id               uuid             NOT NULL
  role_permissions jsonb            NOT NULL
  smtp_host        varchar(255)     NULL
  smtp_port        integer          NULL
  smtp_user        varchar(255)     NULL
  smtp_password    varchar(255)     NULL
  smtp_use_tls     boolean          NULL
  smtp_from        varchar(255)     NULL
"""
import uuid

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyConfig(Base):
    """Read-only mirror — only the columns needed by booking-api."""

    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_permissions = Column(JSONB, nullable=False)

    smtp_host:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls:  Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_from:     Mapped[str | None] = mapped_column(String(255), nullable=True)
