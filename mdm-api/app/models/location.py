"""The plant area tree.

The table was created by migration 0018 and, until now, only ever written by
migrations: mdm-api had no model and no endpoint for it, so the HSE Manager who
owns the content could not add the Line 1 / Line 2 nodes they asked for. This
model exists to give that content a write path in the service that owns it.

Columns verified against information_schema on 2026-09-21 rather than read off
the migration: 0018 created the table and three later migrations reshaped it,
and a model that disagrees with the physical table fails at query time.
"""
import uuid

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    # Materialized path built from names, so renaming a node has to rewrite the
    # path of everything under it. Records that reference a location keep their
    # own snapshot of the path, so old records are unaffected by the rewrite.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    access_area: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether something may be filed against this node itself rather than only
    # against a node beneath it. An operational area with Line 1 and Line 2
    # under it stays selectable at HSE's request: a reporter who does not know
    # which line it happened on would otherwise have to guess, and a guessed
    # line is worse than no line.
    is_selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Process order, not alphabetical: sorting by name puts the warehouse above
    # production and throws away the order HSE gave us.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
