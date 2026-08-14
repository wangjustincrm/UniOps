"""Authz hub tables — roles, permissions, matrix, locks, additional user roles.

Source of truth for the Access Control Matrix (migrated out of epms
company_config.role_permissions JSONB, 2026-07). users.role stays the
PRIMARY role; user_roles holds ADDITIONAL roles (union in /me/permissions).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoleDef(Base):
    __tablename__ = "role_defs"
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # False for ADDITIONAL-ONLY roles (erp_pa_officer / payment_officer): they
    # are granted through user_roles and must never land in users.role. Enforced
    # in put_user_roles and surfaced in /authz/defs so the Portal and EPMS admin
    # dropdowns filter themselves instead of each keeping a hardcoded list.
    assignable_as_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")


class PermissionDef(Base):
    __tablename__ = "permission_defs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RolePermission(Base):
    """Granted cells only — a row means allowed=True."""
    __tablename__ = "role_permissions"
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class RolePermissionLock(Base):
    """Forced-True cells the UI may not edit (seeded from epms LOCKED_PERMISSIONS)."""
    __tablename__ = "role_permission_locks"
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True)


class UserRole(Base):
    """ADDITIONAL roles per user (primary stays users.role)."""
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
