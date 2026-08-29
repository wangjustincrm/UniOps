"""Read-only mirrors of tables owned by other UniOps services.

ehs-api's Alembic migrations DO NOT create or alter any table in this module.
They exist so that:

  1. SQLAlchemy can resolve the foreign keys Safety tables declare against
     `users`.
  2. Safety can JOIN for names and department scoping without an HTTP hop —
     every service shares one physical database.
  3. `Base.metadata.create_all()` materializes them in the test database.

`Task` is the exception: it is a read+write mirror. Safety writes rows into
the `tasks` table so its work lands in the same Portal inbox as purchasing and
approval tasks — the established pattern (see vms-api/app/models/task_mirror.py).
The schema is owned by epms-api.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class User(Base):
    """Read-only mirror of epms-api's `users` — only the columns Safety needs."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Department(Base):
    """Read-only mirror of mdm-api's `departments` (flat: code / name / active)."""

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Location(Base):
    """Read-only mirror of the `locations` tree owned by mdm-api.

    Safety is the first consumer and the HSE Manager maintains the content,
    but the table lives in mdm because it is genuine master data: the future
    maintenance module, VMS access areas and MRP lines all want the same tree.
    Putting it here would mean renaming a foreign-keyed table later.
    """

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    depth: Mapped[int] = mapped_column(nullable=False, default=0)
    access_area: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Task(UUIDPrimaryKey, TimestampMixin, Base):
    """Read+write mirror of epms-api's `tasks`.

    `document_type` is varchar(20) in production (epms-api migration
    t0o1p2q3r4s5 widened it from 10). Safety's document types are kept to ten
    characters anyway — ehs_inc, ehs_act, ehs_cert — so nothing here depends on
    that widening having reached every service's test database.

    Task `type` must never begin with "approve" unless the approval engine
    writes it: epms-api's POST /tasks/{id}/complete refuses to close such tasks
    (app/api/v1/tasks.py) because doing so strands the document mid-approval.
    """

    __tablename__ = "tasks"

    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")

    document_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    document_number: Mapped[str] = mapped_column(String(40), nullable=False)

    assigned_role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
