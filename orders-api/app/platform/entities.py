"""Entity base classes (design §13.1).

Mixins for the business code's own declarative `Base`; inheritance is never
deeper than three levels:

    class Partner(Audited, Versioned, Deactivatable, Base):
        __tablename__ = "<prefix>_partners"

`created_by` / `updated_by` and their names are filled by the audit trail
(audit.py) from the unit of work's actor — never by hand. A row written by the
scheduler carries `created_by = NULL` and `created_by_name = 'SYSTEM'`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func, text, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class Entity:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class CreationAudited(Entity):
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)


class Audited(CreationAudited):
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Versioned:
    """Optimistic locking: an UPDATE whose version no longer matches raises
    StaleDataError, which the services turn into BusinessError('entity.stale')."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:
        return {"version_id_col": cls.__table__.c.version}


class SoftDeletable:
    """Available, not used in v1 (design §13.1). Repositories exclude rows with
    `deleted_at` set for models that opt in; use `partial_unique` for unique
    keys that must ignore deleted rows."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    @classmethod
    def partial_unique(cls, name: str, *columns: str) -> Index:
        """`unique (columns) WHERE deleted_at IS NULL` — put it in __table_args__."""
        return Index(name, *columns, unique=True, postgresql_where=text("deleted_at IS NULL"))


class Deactivatable:
    """Config rows referenced by business rows are deactivated, never deleted."""

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
