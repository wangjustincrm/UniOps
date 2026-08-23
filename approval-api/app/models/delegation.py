"""Dated approval delegation (代班) — owned by approval-api.

epms-api and expense-api read this table read-only via raw SQL (the same
cross-service pattern access_scope._mapped_dept_ids uses for
approval_dept_routing). They have no ORM model for it.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ExcludeConstraint, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApprovalDelegation(Base):
    __tablename__ = "approval_delegations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    delegator_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    delegate_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)   # inclusive
    end_date: Mapped[date] = mapped_column(Date, nullable=False)     # inclusive
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("delegator_user_id <> delegate_user_id",
                        name="ck_delegation_not_self"),
        CheckConstraint("end_date >= start_date", name="ck_delegation_date_order"),
        # One live window per delegator. Partial: revoked rows free their dates.
        ExcludeConstraint(
            ("delegator_user_id", "="),
            (func.daterange(start_date, end_date, "[]"), "&&"),
            name="ex_delegation_no_overlap",
            using="gist",
            where=revoked_at.is_(None),
        ),
    )
