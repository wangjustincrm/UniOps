"""Approval routing rules — owned by approval-api (phase 3).

Replaces the EPMS company_config JSONB trio (dept_gm_opm_mapping /
dept_director_mapping / dept_supervisor_enabled) and the *_backup_user_id
fields of role_management. One row per active department.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeptRouting(Base):
    __tablename__ = "approval_dept_routing"

    dept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # 'gm' | 'opm' — which post approves this department's gm_or_opm step.
    gm_or_opm: Mapped[str] = mapped_column(String(3), nullable=False, server_default="gm")
    director_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Default FALSE: an unlisted department has no supervisor layer today
    # (engine.py:414 + test_engine_optional_levels.py:64). Do not flip this.
    supervisor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ApprovalBackup(Base):
    """Stand-in approver when the primary post-holder is unavailable."""
    __tablename__ = "approval_backups"

    role_code: Mapped[str] = mapped_column(String(50), primary_key=True)   # 'gm' | 'opm'
    backup_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ApprovalSetting(Base):
    """Engine-wide switches that are not per-department.

    `DeptRouting` answers "who approves THIS department's gm_or_opm step". A
    payment application covering purchase orders from several departments has
    no such answer — one department may route to GM and another to OPM — so
    the post that approves those lives here instead.

    Key/value rather than a column per switch: this is admin configuration read
    once per approval action, and a new switch should not need a migration in
    three services' mirrors.
    """
    __tablename__ = "approval_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


# Which post approves a payment application that spans several departments.
CROSS_DEPT_GM_OR_OPM = "cross_department_gm_or_opm"
