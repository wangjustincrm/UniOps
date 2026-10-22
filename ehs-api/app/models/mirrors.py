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

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class User(Base):
    """Read-only mirror of epms-api's `users` — only the columns Safety needs."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # email | teams | both | none. A person who has opted out of email must
    # not be mailed by this module either.
    notification_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    # Whether an incident may be recorded against this node itself, as opposed
    # to only against something beneath it. A column rather than the old
    # "offer only childless nodes" rule, at HSE's request: an operational area
    # with Line 1 and Line 2 under it must stay selectable, because a reporter
    # who does not know which line it happened on would otherwise have to
    # guess, and a guessed line is worse than no line.
    # server_default mirrors the physical column, not just the Python default:
    # the test database is built by create_all from this declaration, and a
    # mirror that omits the server default makes an insert that works in
    # production fail there.
    is_selectable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")
    # HSE listed the general areas with Outside the Plant and Production first,
    # and the operational areas in process order from milk receiving to
    # packaging. Sorting by name throws both away and puts the warehouse above
    # production, so the order is stored rather than derived.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")


class CompanyConfig(Base):
    """Read-only mirror of epms-api's `company_config` — the SMTP block only.

    Safety sends its own mail rather than routing through epms-api, because the
    messages are Safety's (a statutory countdown, a policy to acknowledge) and
    an HTTP hop to another service to send them would add a failure mode with
    nothing in return. It uses the same credentials so there is one mail server
    to configure, and one place to change it.

    Columns verified against information_schema on 2026-09-01 rather than
    assumed: a mirror that disagrees with the physical table fails at query
    time, and this codebase has been caught by that three times.
    """

    __tablename__ = "company_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The logo set under Admin → Company Settings, as a data URL. Round 9: the
    # in-house training certificate carries it.
    logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


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
