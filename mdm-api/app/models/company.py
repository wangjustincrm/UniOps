from datetime import date
from sqlalchemy import Boolean, Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class Company(UUIDPrimaryKey, TimestampMixin, Base):
    """MDM-owned company/organization master. New table in the shared epms DB."""
    __tablename__ = "companies"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    incorporated_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(255), nullable=True)

    primary_address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)

    functional_currency: Mapped[str] = mapped_column(String(10), default="CAD", nullable=False)
    fiscal_year_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "MM-DD"
    fiscal_year_end: Mapped[str | None] = mapped_column(String(5), nullable=True)    # "MM-DD"

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    erp_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
