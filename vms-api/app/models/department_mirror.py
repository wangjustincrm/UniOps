"""Read-only mirror of epms-api's `departments` table.

vms-api does NOT own this table — epms-api is the writer of record. We map only
the columns the CFIA visit-log report needs (`id`, `name`) so the export can
show the host's department NAME instead of its UUID.

Like `user_mirror`, vms-api Alembic migrations DO NOT create or alter this
table; in production it already exists (created by epms-api migrations), and in
tests `Base.metadata.create_all()` materializes the mapped subset.
"""
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Department(Base):
    """Read-only mirror — only the fields vms-api reads."""
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
