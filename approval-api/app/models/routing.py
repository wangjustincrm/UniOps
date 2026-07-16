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
