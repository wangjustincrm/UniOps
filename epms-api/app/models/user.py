"""User ORM model."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Role: system_admin | gm | finance_manager | finance_bp | ap_clerk | opm | requester
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="requester")

    # FK to departments — stored as UUID string for now; proper FK added in Epic 3
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Direct supervisor for optional supervisor-approval workflow step
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # MFA (TOTP)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Microsoft Teams account (email / UPN) for Teams approval workflow
    teams_account: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Force password change on next login (set for new/reset accounts)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # When the password was last set/changed. Used for password-expiry
    # enforcement against company_config.password_expiry_days.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notification delivery preference: email_only | teams_only | both | none
    notification_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email_only")

    # Link to ERP person when this user was imported from ERP MDM
    erp_person_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # True when the user was created via the ERP import flow. ERP-imported users
    # have a read-only ERP Code; manually created users can edit it.
    erp_imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
