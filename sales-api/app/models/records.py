"""The platform's own tables, named for Sales (design §13.7 / §13.13; migration
sales02_audit_outbox). The column shapes come from app/platform; only the
table names are chosen here.

- `sales_audit`: one row per change to an Audited entity and per state
  transition (who, what, before / after, reason) — FR-OB-53.
- `sales_outbox`: external effects written in the business transaction and
  executed after commit (at most once).
- `sales_user_views`: each user's saved views of the lists (§13.19).
- `sales_notifications`: each notification made (FR-OB-56 history; sales13).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.platform.audit import AuditRecordMixin
from app.platform.outbox import OutboxRecordMixin
from app.platform.views import UserViewMixin


class AuditRecord(AuditRecordMixin, Base):
    __tablename__ = "sales_audit"
    # The history of one record (the detail views' audit panel).
    __table_args__ = (Index("ix_sales_audit_entity", "entity", "entity_id", "at"),)


class OutboxRecord(OutboxRecordMixin, Base):
    __tablename__ = "sales_outbox"


class UserView(UserViewMixin, Base):
    __tablename__ = "sales_user_views"
    __table_args__ = (
        # A name once per user and list, whatever its case.
        Index("uq_sales_user_views_name", "user_id", "resource", func.lower(text("name")), unique=True),
        # At most one default view per user and list.
        Index("uq_sales_user_views_default", "user_id", "resource", unique=True,
              postgresql_where=text("is_default")),
    )


class NotificationRecord(Base):
    """A task or an email made for an event (FR-OB-56: "the history of
    notifications sent"). An email's delivery is its outbox row's status."""
    __tablename__ = "sales_notifications"
    __table_args__ = (CheckConstraint("channel IN ('TASK', 'EMAIL')", name="ck_sales_notifications_channel"),
                      Index("ix_sales_notifications_at", "at"), Index("ix_sales_notifications_event_type", "event_type"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    problem_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    recipients: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # no FK: the outbox is pruned on its own
    test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
