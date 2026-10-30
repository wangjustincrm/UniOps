"""The platform's own tables, named for Sales (design §13.7 / §13.13; migration
sales02_audit_outbox). The column shapes come from app/platform; only the
table names are chosen here.

- `sales_audit`: one row per change to an Audited entity and per state
  transition (who, what, before / after, reason) — FR-OB-53.
- `sales_outbox`: external effects written in the business transaction and
  executed after commit (at most once).
- `sales_user_views`: each user's saved views of the lists (§13.19).
"""
from __future__ import annotations

from sqlalchemy import Index, func, text

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
