"""Read-only mirror of epms-api's `users` table.

vms-api does NOT own this table — epms-api is the writer of record and owns
its schema and migrations. Registering the mirror with `Base.metadata` here
lets vms-api:

  1. JOIN against `users` for the dept_manager visibility scope (vms_visits
     has no department_id; we resolve it via host_id → users.department_id).
  2. Look up `full_name` for audit log entries (the JWT payload only carries
     `sub` and `role`).
  3. In tests, have `Base.metadata.create_all()` materialize the table so
     vms_visits.host_id FKs work end-to-end.

The vms-api Alembic migrations DO NOT create or alter this table; it must
already exist (in production: created by epms-api migrations).
"""
import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """Read-only mirror — fields only as needed by vms-api."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
