"""User — mirror of the shared users table (schema owned by epms-api alembic).

Phase 0-B4 moves AUTH code ownership here; table ownership migration is a
later cleanup. Columns are a verbatim copy of epms-api's model.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="requester")
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    teams_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email_only")
    erp_person_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    erp_imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Preset signature (base64 `data:image/...` URL) drawn or uploaded by the
    # user in My Profile. Column lives in epms-api's alembic like every other
    # users column; this is the mirror.
    signature_image: Mapped[str | None] = mapped_column(Text, nullable=True)
