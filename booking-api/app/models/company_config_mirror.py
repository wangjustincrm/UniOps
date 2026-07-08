"""Read-only mirror of epms-api's `company_config` table.

booking-api does NOT own this table — epms-api is the writer of record and
owns its schema and migrations.  Registering the mirror with `Base.metadata`
here lets booking-api:

  1. Query `role_permissions` (JSONB) in `permissions._load_matrix()` to
     enforce the shared Access Control Matrix.
  2. In tests, have `Base.metadata.create_all()` materialise the table so
     `_load_matrix()` can execute without a savepoint guard or bare except.

The booking-api Alembic migrations DO NOT create or alter this table; it must
already exist (in production: created by epms-api migrations).

Physical column types verified against epms DB 2026-07-08:
  id               uuid  NOT NULL
  role_permissions jsonb NOT NULL
"""
import uuid

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class CompanyConfig(Base):
    """Read-only mirror — only the two columns needed by booking-api."""

    __tablename__ = "company_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_permissions = Column(JSONB, nullable=False)
