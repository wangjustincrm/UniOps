from datetime import datetime
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpPerson(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_persons"

    erp_person_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    person_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pk_psndoc: Mapped[str | None] = mapped_column(String(50), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
