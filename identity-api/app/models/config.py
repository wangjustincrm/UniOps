"""CompanyConfig — read-only mirror of the columns auth needs (epms owns schema).

Never INSERT from identity: the physical table has many more NOT NULL columns.
Missing row ⇒ treat as mfa enabled (epms default) with env-fallback SMTP.
"""
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


# NOTE: no TimestampMixin — the physical table has no created_at column
# (verified in prod: only updated_at exists). Mirrors must match reality.
class CompanyConfig(UUIDPrimaryKey, Base):
    __tablename__ = "company_config"

    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_use_tls: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
